---
title: "The Null Slot: What -fno-rtti Actually Removes"
date: 2026-08-26
last_modified_at: 2026-10-01
categories: [C++, Performance]
tags: [rtti, dynamic-cast, typeid, abi, vtable, openjdk, hotspot, gcc, clang, libstdc++, libc++, elf, relocations, benchmarks]
description: >-
  What -fno-rtti actually does to a binary (the typeinfo objects and name strings go, the
  vtable keeps its typeinfo slot and leaves it null, exceptions still emit typeinfo), why large
  projects switch it off, the mixing failure that links clean and segfaults, and what the flag
  saves in size, load time and build time on a synthetic library and on OpenJDK's libjvm.so.
image:
  path: /assets/img/og/the-null-slot-what-fno-rtti-actually-removes.png
  alt: "The Null Slot: What -fno-rtti Actually Removes"
  hero: false
---

Everyone describes `-fno-rtti` the same way: it disables `dynamic_cast` and `typeid`. That is true, and it is about a third of what the flag does.

The part that surprised me is that the vtable does not get any smaller. Compile a polymorphic class with the flag and the typeinfo pointer is still there, still occupying its eight bytes, just holding null. That one detail explains the flag's nastiest failure mode, and a good deal of what follows.

I went looking because OpenJDK builds `libjvm.so` with it and I wanted to know what that buys. What I found fills three posts, and [the last of them]({% post_url 2026-08-28-what-comes-after-fno-rtti-and-how-openjdk-lives-without-rtti %}) gets back to the JVM. Everything reproduces from [the companion repo](https://github.com/Shubhankar-Gambhir/fno-rtti-bench) with `make`.

*Revised 1 October 2026. This version is reorganised and expanded, adds [measurements across GCC 9 to 15, Clang 22 and libc++]({% post_url 2026-08-27-what-dynamic-cast-costs-and-which-runtime-decides %}), and corrects several claims the first version got wrong, chiefly about where its build-time savings came from and which runtime produced its `dynamic_cast` timings.*

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

Almost nobody disables RTTI by itself. Every project surveyed in [What Comes After -fno-rtti, and How OpenJDK Lives Without RTTI]({% post_url 2026-08-28-what-comes-after-fno-rtti-and-how-openjdk-lives-without-rtti %}) (Chromium and V8, Firefox, LLVM, Android's platform build, WebKit, Fuchsia and OpenJDK) pairs `-fno-rtti` with `-fno-exceptions`. The reasons fall into three groups, and they are not equally convincing.

The first is size. Every polymorphic class drags along a name string, a `type_info` object and four relocations, and the next section measures what that adds up to. It is real, and on a real binary it is small.

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

## Impact: Size, Load Time, Build Time

Start with something real. A stock OpenJDK 17.0.0.1 `libjvm.so`:

```
_ZTV (vtables)     3157
_ZTI (typeinfo)      10        <- 315:1
```

All ten survivors are libsupc++ and libstdc++ internals: `__class_type_info`, `std::bad_exception`, `__forced_unwind`. HotSpot's own three thousand polymorphic classes contribute none. Counting `_ZTI` against `_ZTV` makes a decent one-line test for whether any binary was built this way.

To find out what those 3157 null slots are worth, I cloned HotSpot's `BarrierSet` hierarchy shape into 1,800 polymorphic classes under one shared root and built it three ways with GCC 11.4.0, keeping the two flags separate:

| metric | `-frtti` | `-fno-rtti` | `+ -fno-exceptions` |
|---|---:|---:|---:|
| file size (bytes) | 2,747,984 | 2,160,928 (-21.4%) | 2,082,856 (-24.2%) |
| dynamic relocations | 16,219 | 9,016 (-44.4%) | 9,014 (-44.4%) |
| `.rodata`, the `_ZTS` name strings | 159,628 | 44,716 (-72.0%) | 44,716 |
| `.data.rel.ro`, the `_ZTI` objects | 144,072 | 100,856 (-30.0%) | 100,856 |
| `.gcc_except_table` | 23,656 | 23,656 (0%) | 0 (-100%) |
| `_ZTI` symbols | 1,801 | 0 | 0 |
| compile wall time, best of 5 | 2.92 s | 2.86 s (noise) | 1.50 s (-49%) |
| compile CPU, the 20 class files | 11.53 s | 10.72 s (-7%) | 10.65 s (-8%) |
| compile CPU, the factory file | 1.92 s | 1.83 s (noise) | 0.85 s (-56%) |

The build-time rows are mostly about one file. At `-j64` the wall clock waits for the slowest translation unit, and here that is the factory, one function with 1,800 `new` expressions. Each needs a cleanup that frees the memory if the constructor throws, and those cleanups are the whole of `.gcc_except_table`; `-fno-exceptions` deletes them and more than halves the factory. The twenty files that define the classes have no cleanups, so `-fno-exceptions` leaves them alone. They are where the typeinfo gets emitted, though, and `-fno-rtti` trims them by 7 percent, a saving the wall clock never shows because the factory is still running. Neither flag is a build-time flag in general: `-fno-exceptions` pays in proportion to your cleanup code and `-fno-rtti` in proportion to your polymorphic classes. Quoting the pair's savings for either one is how the folklore got muddled.

The 7,203 vanished relocations come to four per class: the vtable's typeinfo slot, the `_ZTI` object's own vptr, its `__name` pointer and its `__base_type` pointer. That is 1,800 times four, plus three for the root, whose `_ZTI` has no base to point at. It gives per-class rates that transfer:

```
_ZTS name strings   (.rodata)       63.8 B/class
_ZTI objects        (.data.rel.ro)  24.0 B/class
dynamic relocations (.rela.dyn)     96.0 B/class   (4.00 relocs/class)
                                   -----------------
                                   183.8 B/class
```

Relocations cost more than the objects they point at, and they are the part people forget. Each one is processed at load time and dirties a page in `.data.rel.ro` that the process can no longer share with anyone. On this corpus that makes `dlopen` 17 to 21 percent faster across two runs (medians of 400 loads, 295 down to 232 microseconds in the first) and leaves 30 percent fewer private-dirty pages, 148 KB down to 104 KB.

The percentages do not transfer. The synthetic library is almost pure vtable, since every method body is `return N;`, while a real `libjvm.so` is overwhelmingly code. The same per-class rates land at 3157 x 184 B, roughly 580 KB on a 21.7 MiB binary. That is two and a half percent, not twenty-one.

Speed is left for [What dynamic_cast Costs, and Which Runtime Decides]({% post_url 2026-08-27-what-dynamic-cast-costs-and-which-runtime-decides %}): the type tag projects use in place of `dynamic_cast`, and `dynamic_cast` itself across seven GCC releases, Clang 22 and libc++.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core.
Compilers: GCC 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 with libc++ 22.1.4, all from conda-forge via micromamba. Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`.
Methodology: size and relocation figures are static ELF accounting and exactly reproducible. Compile wall time is best of 5 at `-j64`; the compile CPU rows are user CPU time for compiling each group of files serially, median of 5. `dlopen` figures are the median of 400 loads, with private-dirty pages read from `/proc/self/smaps` after the final load. The construct matrix is `make constructs`.
JVM figures come from a stock OpenJDK 17.0.0.1 build (`nm -a`, `readelf -rW`); `make jvm` runs the same census against any JDK you point it at.

**Next:** [What dynamic_cast Costs, and Which Runtime Decides]({% post_url 2026-08-27-what-dynamic-cast-costs-and-which-runtime-decides %})
