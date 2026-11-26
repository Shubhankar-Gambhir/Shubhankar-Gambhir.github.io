---
title: "The Null Slot: What -fno-rtti Actually Removes"
date: 2026-08-26
last_modified_at: 2026-10-01
categories: [C++, Performance]
tags: [rtti, dynamic-cast, typeid, abi, vtable, gcc, clang, libstdc++, libc++, elf, relocations]
description: >-
  What -fno-rtti actually does to a binary: the typeinfo objects and name strings go, the
  vtable keeps its typeinfo slot and leaves it null, and exceptions still emit typeinfo. Which
  constructs eight compilers reject under it, why large projects switch it off, and a mixing
  failure that links without a diagnostic and segfaults at the first typeid.
image:
  path: /assets/img/og/the-null-slot-what-fno-rtti-actually-removes.png
  alt: "The Null Slot: What -fno-rtti Actually Removes"
  hero: false
---

Everyone describes `-fno-rtti` the same way: it disables `dynamic_cast` and `typeid`. That is true, and it is about a third of what the flag does.

The part that surprised me is that the vtable does not get any smaller. Compile a polymorphic class with the flag and the typeinfo pointer is still there, still occupying its eight bytes, just holding null. That one detail explains the flag's nastiest failure mode, and a good deal of what follows.

I went looking because OpenJDK builds `libjvm.so` with it and I wanted to know what that buys. What I found fills four posts, and the last of them, [How OpenJDK and Everyone Else Live Without RTTI]({% post_url 2026-08-29-how-openjdk-and-everyone-else-live-without-rtti %}), gets back to the JVM. Everything reproduces from [the companion repo](https://github.com/Shubhankar-Gambhir/fno-rtti-bench) with `make`.

*Revised 1 October 2026. This version is reorganised and expanded, adds [measurements across GCC 9 to 15, Clang 22 and libc++]({% post_url 2026-08-28-which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti %}), and corrects several claims the first version got wrong, chiefly about [where its build-time savings came from]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}) and which runtime produced its `dynamic_cast` timings.*

## What the Flag Is

RTTI, run-time type information, is the machinery behind two operators. `typeid(*p)` hands you a `std::type_info` describing the object's dynamic type; `dynamic_cast<Derived*>(p)` checks at run time whether `p` really points into a `Derived`. To support them, the compiler emits a `type_info` object (`_ZTI`) and its mangled name (`_ZTS`) for every polymorphic class, and plants a pointer to the `type_info` in the class's vtable. `-fno-rtti` tells GCC and Clang to stop emitting that data and to reject the operators that need it. MSVC spells it `/GR-` and draws the line somewhere else.

Compile a two-class hierarchy both ways and diff the symbols:

```
=== WITH RTTI ===                              === WITHOUT RTTI ===
U vtable for __cxxabiv1::__class_type_info     (gone)
U vtable for __cxxabiv1::__si_class_type_info  (gone)
V typeinfo for Base                            (gone)
V typeinfo for Derived                         (gone)
V typeinfo name for Base                       (gone)
V typeinfo name for Derived                    (gone)
V vtable for Base                              V vtable for Base
V vtable for Derived                           V vtable for Derived
```

The typeinfo objects go, and so do the references to the runtime's `__class_type_info` vtables. The vtables stay, and so does their size:

```
poly_rtti.o    .data.rel.ro.local._ZTV4Base   size 0x28   4 relocations
poly_nortti.o  .data.rel.ro.local._ZTV4Base   size 0x28   3 relocations
```

Forty bytes either way. The missing relocation is the typeinfo slot:

```
WITH RTTI                                  WITHOUT RTTI
+0x00   (offset-to-top)                    +0x00   (offset-to-top)
+0x08   -> _ZTI4Base                       +0x08   no relocation, stays NULL
+0x10   -> _ZN4BaseD1Ev   <- vptr points here
+0x18   -> _ZN4BaseD0Ev
+0x20   -> _ZN4Base1fEv
```

You can read that slot yourself, and the way you do it is the way the compiler does. A polymorphic object's first eight bytes are its vptr, and the vptr points at the first virtual function slot (`+0x10` in that layout), not at the start of the vtable. The typeinfo pointer therefore lives one slot *before* where the vptr points:

```cpp
void* ti = (*reinterpret_cast<void***>(p))[-1];   // load the vptr, step back one slot
```

That is undefined behavior that happens to work because the Itanium C++ ABI fixes the layout. Past a null check, it is also exactly what `typeid(*p)` compiles to ([Compiler Explorer](https://godbolt.org/z/f6sjnhqdb)):

```nasm
        testq   %rdi, %rdi          ; typeid(*nullptr) must throw bad_typeid...
        je      .L2                 ; ...so that lives on a cold path
        movq    (%rdi), %rax        ; load the vptr from the object's first 8 bytes
        movq    -8(%rax), %rax      ; vptr[-1]: the std::type_info* for the dynamic type
        ret                         ; return it
```

On an object whose vtable came from a `-fno-rtti` translation unit, that second load returns `(nil)`. The same probe on GCC 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 gives a `0x28` vtable every time, with the flag and without it. The ABI pins the layout, and no release has moved it.

The layout survives the flag. Virtual function slots sit at the same offsets, objects are the same size, and virtual calls compile to identical code. That is deliberate. Had the flag reclaimed the eight bytes, every `-fno-rtti` object file would be silently incompatible with every `-frtti` one, and the symptom would be calls landing on the wrong function.

What the flag rejects barely varies. I compiled eleven one-line constructs under `-fno-rtti` with all eight of those compilers:

| Construct | With `-fno-rtti` |
|---|---|
| `typeid(*p)`, `typeid(T)`, `typeid` of a plain struct | error on every compiler |
| `dynamic_cast` down or across a hierarchy | error on every compiler |
| `dynamic_cast` up, or to `void*` | compiles |
| `throw` and `catch`, including catch-by-base | compiles and works |
| `std::any_cast<T>` | compiles |
| `std::any::type()`, `std::function::target_type()` | error: the library removes them |
| `std::function::target<T>()` | error on GCC 9.5.0 and 10.4.0, compiles from GCC 11.4.0 and on Clang |

A plain struct's `typeid` needs no run-time lookup at all, and both compilers still refuse it, so the rule is about the operator rather than the data. Upcasts and casts to `void*` survive because they need no type information: an upcast is resolved at compile time, and a cast to `void*` only needs the offset-to-top already sitting in the vtable. The [GCC manual](https://gcc.gnu.org/onlinedocs/gcc-15.2.0/gcc/C_002b_002b-Dialect-Options.html) says these "casts to `void *` or to unambiguous base classes" still work.

Exceptions are the row that matters most. They keep working, and they keep emitting typeinfo:

```
$ g++ -fno-rtti -O2 -c eh.cpp && nm -C eh.o | grep typeinfo
V typeinfo for MyErr
V typeinfo for Sub
V typeinfo name for MyErr
V typeinfo name for Sub
```

The Itanium ABI matches catch handlers by comparing `type_info` objects, so the compiler generates them on demand whatever you asked for. In the manual's words, "exception handling uses the same information, but G++ generates it as needed." On its own, `-fno-rtti` leaves a lot of typeinfo behind.

## Why Codebases Turn It Off, and Who Should

Almost nobody disables RTTI by itself. Every project surveyed in [How OpenJDK and Everyone Else Live Without RTTI]({% post_url 2026-08-29-how-openjdk-and-everyone-else-live-without-rtti %}) (Chromium and V8, Firefox, LLVM, Android's platform build, WebKit, Fuchsia and OpenJDK) pairs `-fno-rtti` with `-fno-exceptions`. The reasons fall into three groups, and they are not equally convincing.

The first is size. Every polymorphic class drags along a name string, a `type_info` object and four relocations, and [the next post]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}) measures what that adds up to. It is real, and on a real binary it is small.

The second is style. Google's C++ style guide says ["Avoid using run-time type information (RTTI)"](https://google.github.io/styleguide/cppguide.html#Run-Time_Type_Information__RTTI_) and, a few sections earlier, ["We do not use C++ exceptions."](https://google.github.io/styleguide/cppguide.html#Exceptions) Chromium's build files carry [the note](https://chromium.googlesource.com/chromium/src/+/refs/tags/154.0.8037.92/build/config/compiler/BUILD.gn#2374) "exceptions are disallowed in Google code"; Android's Soong switches them off because ["Google C++ style does not allow exceptions"](https://android.googlesource.com/platform/build/soong/+/refs/tags/android-17.0.0_r1/cc/config/global.go#141). Underneath is the argument that code which branches on an object's dynamic type is usually a missing virtual function. HotSpot's style guide makes the same case in nearly the same words.

The third is the one I find strongest: a compiler-enforced guarantee that nothing in the binary performs run-time type introspection. That matters where the C++ runs somewhere unusual. HotSpot's runs inside signal handlers and GC pauses, and Fuchsia builds its kernel this way. A `dynamic_cast` there is an out-of-line call into the C++ runtime whose cost depends on the hierarchy and on the answer, and a `dynamic_cast` to a reference can throw. The flag makes all of that impossible to write rather than merely discouraged.

Adopting it is not free, though. The cost people discover last is mixing. An `-frtti` class deriving from an `-fno-rtti` base fails at link time, which is the case the GCC manual warns about:

```
ld: dd.o:(.data.rel.ro._ZTI2D2[typeinfo for D2]+0x10):
    undefined reference to `typeinfo for Base'
collect2: error: ld returned 1 exit status
```

The reverse direction is worse. `-frtti` code calling `typeid` on an object whose vtable came from an `-fno-rtti` translation unit links with no diagnostic at all:

```
$ g++ lib_nortti.o app_rtti.o -o app_n     # links successfully, no diagnostic
$ ./app_n
vptr[-1] (typeinfo slot) = (nil)
about to call typeid...
Segmentation fault (core dumped)
```

`typeid(*p)` never names a typeinfo symbol at link time. It fetches one through the vtable, gets null, and faults. `dynamic_cast` also crashes when every virtual function is inline, and fails to link when the class has an out-of-line key function. Ship a `-fno-rtti` library with public polymorphic types and every consumer building with default flags is one `typeid` away from this. Whether a library is built that way is part of its API contract.

Then there is the standard library. libstdc++ and libc++ both remove `std::any::type()` and `std::function::target_type()` under `-fno-rtti`, which at least fails loudly. `std::get_deleter` is where they part ways: libc++ removes it, while libstdc++ keeps it and [returns null unconditionally](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/bits/shared_ptr.h;hb=releases/gcc-15.2.0#l96). Code that compiles under both flags quietly behaves differently.

Sanitizers notice too. With `-fsanitize=undefined`, GCC 15.2.0 and Clang 22.1.4 both silently drop the `vptr` check, which needs the typeinfo you just deleted. Clang rejects an explicit `-fsanitize=vptr`; GCC drops even that without a word. Chromium turns RTTI back on for exactly those sanitizer builds, and Firefox re-enables it for one directory because "ICU requires RTTI".

If you already avoid `dynamic_cast` and `typeid`, control the whole link (or will document the contract), and are switching off exceptions in the same change, the flag is a reasonable thing to adopt. If you ship polymorphic types to consumers you don't control, or your only reason is size, I'd leave it alone.

Size is the reason given most often, and it is also the easiest one to put a number on, so [What -fno-rtti Saves, and What dynamic_cast Costs]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}) starts there, with a stock `libjvm.so` and a synthetic library that is almost nothing but vtables.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake).
Compilers: GCC 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 with libc++ 22.1.4, all from conda-forge via micromamba. Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`.
Methodology: nothing in this post is timed. Symbol, size and relocation figures are static ELF accounting and exactly reproducible; the construct matrix is `make constructs`.

**Next:** [What -fno-rtti Saves, and What dynamic_cast Costs]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %})
