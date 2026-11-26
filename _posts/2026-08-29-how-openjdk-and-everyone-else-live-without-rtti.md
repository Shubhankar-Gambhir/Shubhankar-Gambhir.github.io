---
title: "How OpenJDK and Everyone Else Live Without RTTI"
date: 2026-08-29
categories: [C++, Performance]
tags: [rtti, dynamic-cast, typeid, vtable, abi, openjdk, hotspot, llvm]
description: >-
  Where OpenJDK switches RTTI off and what HotSpot uses instead of dynamic_cast (a tag set, a
  kind field, C2's class ids), the Windows build that kept RTTI on because the Serviceability
  Agent depends on unique vtables, how Chromium, Firefox, LLVM, Android, WebKit, Fuchsia and
  Qt handle the same flag, and what -fno-rtti actually buys.
image:
  path: /assets/img/og/how-openjdk-and-everyone-else-live-without-rtti.png
  alt: "How OpenJDK and Everyone Else Live Without RTTI"
  hero: false
---

HotSpot on Windows has been built with RTTI the whole time, by accident, and when someone tried to fix that in 2023 the accident turned out to be load-bearing. Under GCC and Clang the JVM builds with `-fno-rtti`, the flag this four-part series has been taking apart since [The Null Slot: What -fno-rtti Actually Removes]({% post_url 2026-08-26-the-null-slot-what-fno-rtti-actually-removes %}): no `type_info` for any polymorphic class, a null where each vtable's typeinfo pointer used to be, and no `typeid` or downcasting `dynamic_cast`. HotSpot replaces them with several type checks of its own, and nearly every large C++ project that made the same choice built something similar.

## OpenJDK, and Everyone Else

In OpenJDK the flag lives in one line of [`make/autoconf/flags-cflags.m4`](https://github.com/openjdk/jdk/blob/jdk-17%2B35/make/autoconf/flags-cflags.m4#L448-L452), identical in [JDK 25](https://github.com/openjdk/jdk/blob/jdk-25%2B36/make/autoconf/flags-cflags.m4#L524-L528):

```
TOOLCHAIN_CFLAGS_JVM="-pipe -fno-rtti -fno-exceptions \
    -fvisibility=hidden -fno-strict-aliasing -fno-omit-frame-pointer"
```

It applies to the JVM only. The rest of the JDK's native code gets `TOOLCHAIN_CFLAGS_JDK`, which is `-pipe -fstack-protector` under GCC, and you can see the difference in the shipped bits: in the same JDK 17 install, `libjimage.so` carries typeinfo for `ZipDecompressor`, `ImageDecompressor` and `Endian`. The [HotSpot style guide](https://github.com/openjdk/jdk/blob/jdk-25%2B36/doc/hotspot-style.md#L471-L481) gives the rationale:

> Other than to implement exceptions (which HotSpot doesn't use), most potential uses of RTTI are better done via virtual functions. Some of the remainder can be replaced by bespoke mechanisms. The cost of the additional runtime data structures needed to support RTTI are deemed not worthwhile, given the alternatives.

The source keeps to it. There are no uses of `dynamic_cast` or `typeid` in `src/hotspot` in either JDK 17 or JDK 25. The bespoke mechanisms come in several flavours. [`FakeRttiSupport`](https://github.com/openjdk/jdk/blob/jdk-25%2B36/src/hotspot/share/utilities/fakeRttiSupport.hpp#L31-L52), the tag set [the `Shape` example]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}) is modelled on, arrived with [JDK-8069016](https://bugs.openjdk.org/browse/JDK-8069016) in JDK 9 for the GC's `BarrierSet` hierarchy, and its `barrier_set_cast<T>()` is the same assert-then-`static_cast` as `shape_cast`. `Klass` type checks compare a `KlassKind` field, since [JDK-8283574](https://bugs.openjdk.org/browse/JDK-8283574) in JDK 19. C2's IR nodes carry a class-id bitfield, and [`node.hpp`](https://github.com/openjdk/jdk/blob/jdk-25%2B36/src/hotspot/share/opto/node.hpp#L883-L1027) stamps out `is_X()`, `as_X()` and `isa_X()` for 132 node classes from it with a `DEFINE_CLASS_QUERY` macro. A one-bit-per-class tag set would run out at 64 classes. The class id instead spells out each node's path down the tree, a subclass extending its parent's bit pattern, so `is_X()` is a mask and a compare and all 132 classes fit in 32 bits. `CollectedHeap` exposes a `kind()` enum, and `named_heap<T>()` asserts on it before a `static_cast`.

The `TOOLCHAIN_CFLAGS_JVM` line covers GCC and Clang, and in JDK 17 the AIX compiler gets `-qnortti -qnoeh`. The MSVC line has no `/GR-`, so HotSpot on Windows has always been built with RTTI. Nobody intended that, and in 2023 someone tried to fix it. Turning RTTI off "will reduce the binary size for Hotspot by at least 1 MB", says [JDK-8302817](https://bugs.openjdk.org/browse/JDK-8302817), and the bug exists because doing so broke the Serviceability Agent, HotSpot's out-of-process debugger. The SA identifies a HotSpot object's type by its vtable address. With RTTI on, every MSVC vftable carries a pointer to its own RTTI record and is therefore unique. With RTTI off, identical vtables were folded together, and the SA started reporting `JavaThread`s as `NotificationThread`s, a subclass that overrides no virtual function. A test turned up more duplicates, including `InstanceKlass` and `InstanceClassLoaderKlass`. David Holmes summed it up: "We have been lucky that RTTI was unintentionally left on for Windows builds, and that it did in fact cause vtables to be unique." The bug was closed as Won't Fix, on the judgement that "reducing the size of jvm.dll is not worth the risk of breaking SA", and the build change, [JDK-8303166](https://bugs.openjdk.org/browse/JDK-8303166), followed it. The style guide says only that RTTI "is disabled by the build configuration for some platforms".

Vtable uniqueness is [the assumption libc++ makes about `type_info`, the assumption Clang's `final` optimization makes about vtables]({% post_url 2026-08-28-which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti %}), and the one Microsoft's linker does not promise. Every scheme that identifies a type by an address inherits it.

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

Add it up and size is the weakest argument in the pile. Two and a half percent of [a real binary]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}), a single megabyte in OpenJDK's Windows attempt, weighed against losing `dynamic_cast`, a standard library with holes in it, sanitizers that quietly check less, and [a mixing failure that links clean and crashes later]({% post_url 2026-08-26-the-null-slot-what-fno-rtti-actually-removes %}). If size is all you want, `-ffunction-sections -Wl,--gc-sections` is the first thing to try, and it costs nothing socially. [Relative vtables]({% post_url 2026-08-28-which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti %}) save more than `-fno-rtti` does, if you control every byte of the program.

What the flag actually buys is an invariant the compiler enforces: no run-time type introspection anywhere in this binary. The projects that use it all made that design decision first, built their own cheap, predictable type checks, and then used the flag to keep it true.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake).
Compilers: GCC 11.4.0 from conda-forge via micromamba, for the per-class size rates behind the closing section's two and a half percent. Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`.
Methodology: nothing in this post is timed. Size and relocation figures are static ELF accounting and exactly reproducible.
JVM figures come from a stock OpenJDK 17.0.0.1 build (`nm -a`, `readelf -rW`); `make jvm` runs the same census against any JDK you point it at. Source citations are pinned to jdk-17+35 and jdk-25+36.

**Previously:** [Which Runtime Answers dynamic_cast, and What Comes After -fno-rtti]({% post_url 2026-08-28-which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti %})
