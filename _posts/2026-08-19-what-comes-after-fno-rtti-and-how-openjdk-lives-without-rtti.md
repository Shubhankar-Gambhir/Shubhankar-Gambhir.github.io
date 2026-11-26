---
title: "What Comes After -fno-rtti, and How OpenJDK Lives Without RTTI"
date: 2026-08-19
last_modified_at: 2026-10-01
categories: [C++, Performance]
tags: [rtti, dynamic-cast, typeid, vtable, abi, openjdk, hotspot, llvm, clang, reflection, relocations]
description: >-
  Clang's experimental flags that delete the null slot or make vtables relative, what C++26
  static reflection can and cannot replace, how OpenJDK builds HotSpot without RTTI (a tag set,
  kind fields, C2's class ids, and a Windows build that kept RTTI on by accident), how other
  large projects handle the flag, and what -fno-rtti actually buys.
image:
  path: /assets/img/og/what-comes-after-fno-rtti-and-how-openjdk-lives-without-rtti.png
  alt: "What Comes After -fno-rtti, and How OpenJDK Lives Without RTTI"
  hero: false
---

A vtable built with `-fno-rtti` is exactly as big as one built without it, and Clang has two experimental flags that change that.

Why the typeinfo slot stays, holding null, is covered back at the start of this three-part series, in [The Null Slot: What -fno-rtti Actually Removes]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %}): reclaiming it would make every `-fno-rtti` object file silently incompatible with every `-frtti` one.

## What's Coming

Clang 18 added `-fexperimental-omit-vtable-rtti` ([f45f1c3585e6](https://github.com/llvm/llvm-project/commit/f45f1c3585e6)), which finally deletes [the null slot]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %}), the typeinfo pointer that plain `-fno-rtti` leaves in every vtable so the layout doesn't change. It is a front-end-only flag, so you pass it as `-Xclang -fexperimental-omit-vtable-rtti`, and it refuses to run without `-fno-rtti`, with an error message that reads, typo included, "call only be used with -fno-rtti". It breaks the ABI in exactly the way plain `-fno-rtti` was designed not to, which is why it is experimental. Fuchsia already uses it for its kernel.

Relative vtables go further. `-fexperimental-relative-c++-abi-vtables` stores each slot as a 32-bit offset from the vtable instead of a 64-bit absolute pointer, which halves the vtable and removes the load-time relocations entirely. Clang makes it [the default when targeting Fuchsia](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.4/clang/include/clang/Basic/TargetCXXABI.h#L67-L69). Here is what both do to the 1800-class corpus [behind the size measurements]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %}), built with Clang 22.1.4:

| metric | `-frtti` | `-fno-rtti` | `+ omit-vtable-rtti` | `+ relative vtables` |
|---|---:|---:|---:|---:|
| file size (bytes) | 2,772,608 | 2,226,512 | 2,210,128 | 1,956,104 |
| dynamic relocations | 16,219 | 9,016 | 9,016 | 11 |
| vtable bytes | 100,856 | 100,856 | 86,448 | 50,428 |
| private-dirty after `dlopen` | 148 KB | 104 KB | 92 KB | 8 KB |
| `dlopen`, median of 400 | 256 µs | 223 µs | 198 µs | 180 µs |

Omitting the slot saves exactly 8 bytes times 1,801 vtables and not a single relocation, since a null slot never had one; the vtables pack into fewer pages, which accounts for the smaller dirty footprint. With no relocation work removed, I would not trust its faster `dlopen`. Relative vtables are the dramatic column: private-dirty memory falls from 104 KB to 8 KB because there is almost nothing left for the loader to patch. Combining the two gets the vtables down to 43,224 bytes. The catch is that the whole program must agree, C++ runtime included. Linked against an ordinary libstdc++, a relative-vtable build crashes as soon as it catches a `std::exception&` and calls `what()`. That is fine for an operating system that builds everything from source, and a non-starter for anyone else.

Only one change on the horizon has made it into a standard, and it comes from the language rather than the ABI. C++26 adopted static reflection, [P2996](https://wg21.link/P2996), in June 2025, and GCC 16 ships it behind `-std=c++26 -freflection`; upstream Clang does not have it yet. Reflection happens at compile time and needs no typeinfo, so it works under `-fno-rtti`. On GCC 16.1 this compiles with the flag and prints the type's name:

```cpp
const char* static_name(Base* p) {
  return std::define_static_string(std::meta::display_string_of(^^decltype(*p)));
}
```

What it prints is `Base&`. Reflection sees the static type; `typeid` on the same pointer to a `Derived` would have reported `Derived`. So reflection replaces the static uses of `typeid`, such as type names for logging, enum-to-string tables and serialization, which are exactly the uses that tempt projects to turn RTTI back on. It does nothing for questions about dynamic type. Decoupling exceptions from typeinfo would be the other half, and the proposals that tried, [P0709](https://wg21.link/P0709) and [P3166](https://wg21.link/P3166), have not been adopted.

## OpenJDK, and Everyone Else

In OpenJDK the flag lives in one line of [`make/autoconf/flags-cflags.m4`](https://github.com/openjdk/jdk/blob/jdk-17%2B35/make/autoconf/flags-cflags.m4#L448-L452), identical in [JDK 25](https://github.com/openjdk/jdk/blob/jdk-25%2B36/make/autoconf/flags-cflags.m4#L524-L528):

```
TOOLCHAIN_CFLAGS_JVM="-pipe -fno-rtti -fno-exceptions \
    -fvisibility=hidden -fno-strict-aliasing -fno-omit-frame-pointer"
```

It applies to the JVM only. The rest of the JDK's native code gets `TOOLCHAIN_CFLAGS_JDK`, which is `-pipe -fstack-protector` under GCC, and you can see the difference in the shipped bits: in a stock JDK 17 install, where HotSpot's own classes contribute no typeinfo to `libjvm.so`, `libjimage.so` carries typeinfo for `ZipDecompressor`, `ImageDecompressor` and `Endian`. The [HotSpot style guide](https://github.com/openjdk/jdk/blob/jdk-25%2B36/doc/hotspot-style.md#L471-L481) gives the rationale:

> Other than to implement exceptions (which HotSpot doesn't use), most potential uses of RTTI are better done via virtual functions. Some of the remainder can be replaced by bespoke mechanisms. The cost of the additional runtime data structures needed to support RTTI are deemed not worthwhile, given the alternatives.

The source keeps to it. There are no uses of `dynamic_cast` or `typeid` in `src/hotspot` in either JDK 17 or JDK 25. The bespoke mechanisms come in several flavours. [`FakeRttiSupport`](https://github.com/openjdk/jdk/blob/jdk-25%2B36/src/hotspot/share/utilities/fakeRttiSupport.hpp#L31-L52), the tag set behind the `Shape` example in [What dynamic_cast Costs, and Which Runtime Decides]({% post_url 2026-08-04-what-dynamic-cast-costs-and-which-runtime-decides %}), arrived with [JDK-8069016](https://bugs.openjdk.org/browse/JDK-8069016) in JDK 9 for the GC's `BarrierSet` hierarchy, and its `barrier_set_cast<T>()` is the same assert-then-`static_cast` as that example's `shape_cast`. `Klass` type checks compare a `KlassKind` field, since [JDK-8283574](https://bugs.openjdk.org/browse/JDK-8283574) in JDK 19. C2's IR nodes carry a class-id bitfield, and [`node.hpp`](https://github.com/openjdk/jdk/blob/jdk-25%2B36/src/hotspot/share/opto/node.hpp#L883-L1027) stamps out `is_X()`, `as_X()` and `isa_X()` for 132 node classes from it with a `DEFINE_CLASS_QUERY` macro. A one-bit-per-class tag set would run out at 64 classes. The class id instead spells out each node's path down the tree, a subclass extending its parent's bit pattern, so `is_X()` is a mask and a compare and all 132 classes fit in 32 bits. `CollectedHeap` exposes a `kind()` enum, and `named_heap<T>()` asserts on it before a `static_cast`.

The `TOOLCHAIN_CFLAGS_JVM` line covers GCC and Clang, and in JDK 17 the AIX compiler gets `-qnortti -qnoeh`. The MSVC line has no `/GR-`, so HotSpot on Windows has always been built with RTTI. Nobody intended that, and in 2023 someone tried to fix it. Turning RTTI off "will reduce the binary size for Hotspot by at least 1 MB", says [JDK-8302817](https://bugs.openjdk.org/browse/JDK-8302817), and the bug exists because doing so broke the Serviceability Agent, HotSpot's out-of-process debugger. The SA identifies a HotSpot object's type by its vtable address. With RTTI on, every MSVC vftable carries a pointer to its own RTTI record and is therefore unique. With RTTI off, identical vtables were folded together, and the SA started reporting `JavaThread`s as `NotificationThread`s, a subclass that overrides no virtual function. A test turned up more duplicates, including `InstanceKlass` and `InstanceClassLoaderKlass`. David Holmes summed it up: "We have been lucky that RTTI was unintentionally left on for Windows builds, and that it did in fact cause vtables to be unique." The bug was closed as Won't Fix, on the judgement that "reducing the size of jvm.dll is not worth the risk of breaking SA", and the build change, [JDK-8303166](https://bugs.openjdk.org/browse/JDK-8303166), followed it. The style guide says only that RTTI "is disabled by the build configuration for some platforms".

Vtable uniqueness is [the assumption libc++ makes about `type_info`, the assumption Clang's `final` optimization makes about vtables]({% post_url 2026-08-04-what-dynamic-cast-costs-and-which-runtime-decides %}), and the one Microsoft's linker does not promise. Every scheme that identifies a type by an address inherits it.

OpenJDK is in good company. Here is how other large codebases handle the flag, each pinned to a release:

| project | where RTTI goes off | exceptions too? | what replaces `dynamic_cast` | notes |
|---|---|---|---|---|
| Chromium 154, and V8 through it | [`config("no_rtti")`](https://chromium.googlesource.com/chromium/src/+/refs/tags/154.0.8037.92/build/config/compiler/BUILD.gn#2310), a default config | yes | Blink's [`IsA<T>`/`To<T>`/`DynamicTo<T>`](https://chromium.googlesource.com/chromium/src/+/refs/tags/154.0.8037.92/third_party/blink/renderer/platform/wtf/casting.h#16); V8's [`Is<T>`/`Cast<T>`](https://github.com/v8/v8/blob/15.4.80.19/src/objects/casting.h#L148) | `use_rtti` turns it back on for CFI-diagnostic and UBSan-vptr builds |
| Firefox 157 | [`toolchain.configure`](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_157_0_RELEASE/build/moz.configure/toolchain.configure#L3755-L3767) | yes | `IsElement()`/`AsElement()`, [`FromNode()`](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_157_0_RELEASE/dom/base/nsINode.h#L662) | `--enable-cpp-rtti` for debugging; [ICU is built with `-frtti`](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_157_0_RELEASE/config/external/icu/defs.mozbuild#L39-L41) |
| LLVM 22.1.4 | [`LLVM_ENABLE_RTTI`, default `OFF`](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.4/llvm/cmake/modules/HandleLLVMOptions.cmake#L1174-L1179) | yes | [`isa<>`/`cast<>`/`dyn_cast<>`](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.4/llvm/include/llvm/Support/Casting.h#L547) | enabling exceptions without RTTI is a configure error |
| Android 17 platform | Soong, [per module](https://android.googlesource.com/platform/build/soong/+/refs/tags/android-17.0.0_r1/cc/compiler.go#605) | yes | Binder's [`interface_cast<I>()`](https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-17.0.0_r1/libs/binder/include/binder/IInterface.h#47) | `rtti: true` per module |
| WebKit (WPE 2.54.0) | [`WebKitCompilerFlags.cmake`](https://github.com/WebKit/WebKit/blob/wpewebkit-2.54.0/Source/cmake/WebKitCompilerFlags.cmake#L199-L204) | yes | [`is<>`/`downcast<>`/`dynamicDowncast<>`](https://github.com/WebKit/WebKit/blob/wpewebkit-2.54.0/Source/WTF/wtf/TypeCasts.h#L59) | not applied to clang-cl builds |
| Fuchsia | [`no_rtti`](https://fuchsia.googlesource.com/fuchsia/+/ef4ddc72d3f889705a15e24d9a7d24d0bc09568a/build/config/BUILD.gn#383), a default config | yes | the kernel's [`DownCastDispatcher<T>()`](https://fuchsia.googlesource.com/fuchsia/+/ef4ddc72d3f889705a15e24d9a7d24d0bc09568a/zircon/kernel/object/include/object/dispatcher.h#620) | the kernel also omits the vtable slot |
| Qt 6.12, the exception | not disabled | n/a | [`qobject_cast`](https://github.com/qt/qtbase/blob/v6.12.0/src/corelib/kernel/qobject.h#L432), via moc's `QMetaObject` | user code can opt out with `CONFIG+=rtti_off` |

Every project that disables RTTI also disables exceptions, and most keep a way back in for the code that needs it, usually sanitizers or ICU. Nearly all of them also built a checked cast of their own. That makes the Google style guide's other instruction on the topic amusing: ["Do not hand-implement an RTTI-like workaround. The arguments against RTTI apply just as much to workarounds like class hierarchies with type tags."](https://google.github.io/styleguide/cppguide.html#Run-Time_Type_Information__RTTI_) Chromium, V8, LLVM, WebKit and HotSpot all did exactly that, and are none the worse for it. The Android NDK is split, too: [ndk-build defaults to `-fno-rtti`](https://developer.android.com/ndk/guides/cpp-support#rtti) while CMake builds leave RTTI on.

Qt went the other way. It dropped its Windows `-no-rtti` configure option in 5.9 ["as Qt fails to build under that condition"](https://github.com/qt/qtbase/blob/v5.9.0/dist/changes-5.9.0#L516-L518), and it builds itself with RTTI on. Its own cast, `qobject_cast`, walks moc-generated metadata instead, and [its documentation](https://github.com/qt/qtbase/blob/v6.12.0/src/corelib/kernel/qobject.cpp#L1279-L1282) notes that it "doesn't require RTTI support and it works across dynamic library boundaries". That last clause is the uniqueness problem again, solved by comparing metadata rather than addresses.

## A Design Flag Wearing a Size Flag's Clothes

Add it up and size is the weakest argument in the pile. Two and a half percent of a real binary, a single megabyte in OpenJDK's Windows attempt, weighed against losing `dynamic_cast`, a standard library with holes in it, sanitizers that quietly check less, and [a mixing failure that links clean and crashes later]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %}). If size is all you want, `-ffunction-sections -Wl,--gc-sections` is the first thing to try, and it costs nothing socially. Relative vtables save more than `-fno-rtti` does, if you control every byte of the program.

What the flag actually buys is an invariant the compiler enforces: no run-time type introspection anywhere in this binary. The projects that use it all made that design decision first, built their own cheap, predictable type checks, and then used the flag to keep it true.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core.
Compilers: Clang 22.1.4 for the vtable experiments, and GCC 11.4.0 for the per-class size rates behind the closing section's two and a half percent, both from conda-forge via micromamba. Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`.
Methodology: size and relocation figures are static ELF accounting and exactly reproducible. `dlopen` figures are the median of 400 loads, with private-dirty pages read from `/proc/self/smaps` after the final load. The Clang vtable experiments are `make future`.
JVM figures come from a stock OpenJDK 17.0.0.1 build (`nm -a`, `readelf -rW`); `make jvm` runs the same census against any JDK you point it at. Source citations are pinned to jdk-17+35 and jdk-25+36.

**Previously:** [What dynamic_cast Costs, and Which Runtime Decides]({% post_url 2026-08-04-what-dynamic-cast-costs-and-which-runtime-decides %})
