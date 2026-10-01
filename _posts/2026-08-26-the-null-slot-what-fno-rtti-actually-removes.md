---
title: "The Null Slot: What -fno-rtti Removes, Costs, and Buys"
date: 2026-08-26
last_modified_at: 2026-10-01
categories: [C++, Performance]
tags: [rtti, dynamic-cast, typeid, abi, vtable, openjdk, hotspot, llvm, gcc, clang, libstdc++, libc++, reflection, elf, relocations, benchmarks]
description: >-
  What -fno-rtti actually does to a binary (the vtable keeps its typeinfo slot and it goes
  null), why Chromium, Firefox, LLVM, Android and OpenJDK all turn it off, what it saves in
  size, load time and build time, how dynamic_cast's cost moved across GCC 9 to 15, Clang 22
  and libc++, what Clang and C++26 add next, and how HotSpot lives without it.
image:
  path: /assets/img/og/the-null-slot-what-fno-rtti-actually-removes.png
  alt: "The Null Slot: What -fno-rtti Removes, Costs, and Buys"
  hero: false
---

Everyone describes `-fno-rtti` the same way: it disables `dynamic_cast` and `typeid`. That is true, and it is about a third of what the flag does.

The part that surprised me is that the vtable does not get any smaller. Compile a polymorphic class with the flag and the typeinfo pointer is still there, still occupying its eight bytes, just holding null. That one detail explains the flag's nastiest failure mode, and a good deal of what follows.

I went looking because OpenJDK builds `libjvm.so` with it and I wanted to know what that buys. This post is the long answer: what the flag is, why so many large codebases use it and whether you should, what it saves and costs, what has changed underneath it across compilers and runtime libraries, what is coming next, and how OpenJDK and its peers live without `dynamic_cast`. Everything reproduces from [the companion repo](https://github.com/Shubhankar-Gambhir/fno-rtti-bench) with `make`.

*Revised 1 October 2026. This version is reorganised and expanded, adds measurements across GCC 9 to 15, Clang 22 and libc++, and corrects several claims the first version got wrong, chiefly about where its build-time savings came from and which runtime produced its `dynamic_cast` timings.*

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

You can read that slot yourself, and the way you do it is the way the compiler does. A polymorphic object's first eight bytes are its vptr, and the vptr points at the first virtual function slot (`+0x10` above), not at the start of the vtable. The typeinfo pointer therefore lives one slot *before* where the vptr points:

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

So the flag changes content, not shape. Virtual function slots sit at the same offsets, objects are the same size, and virtual calls compile to identical code. That is deliberate. Had the flag reclaimed the eight bytes, every `-fno-rtti` object file would be silently incompatible with every `-frtti` one, and the symptom would be calls landing on the wrong function. Keeping the slot trades eight bytes per class for a null dereference you can debug.

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

Almost nobody disables RTTI by itself. Every project in the survey at the end of this post (Chromium and V8, Firefox, LLVM, Android's platform build, WebKit, Fuchsia and OpenJDK) pairs `-fno-rtti` with `-fno-exceptions`. The reasons fall into three groups, and they are not equally convincing.

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

So who should use it? You should if you already avoid `dynamic_cast` and `typeid`, if you control the whole link or will document the contract, and if you are switching off exceptions in the same change. You probably should not if you ship polymorphic types to consumers you do not control, or if your only reason is size.

## Impact: Size, Load Time, Build Time, Speed

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

Speed is the subtler question. The flag changes nothing about code that compiles both ways, because virtual calls are identical. What matters is what you replace `dynamic_cast` with, and the usual answer is a type tag. A plain enum works for flat hierarchies; for deep ones, a tag *set* does better. Every class gets a bit, each constructor adds its own on the way up, and the finished object carries the set of every class it is an instance of. A checked downcast then becomes a single bit test against a constant. HotSpot calls its version `FakeRttiSupport`, and [an earlier post]({% post_url 2026-05-12-lazy-resolution-resolve-once-dispatch-forever %}) walks through it. Stripped down to its core, it drops into any hierarchy:

```cpp
template<typename Tag>
class FakeRtti {
public:
  explicit FakeRtti(Tag concrete) : _tag_set(bit(concrete)) {}
  FakeRtti add_tag(Tag t) const { FakeRtti r = *this; r._tag_set |= bit(t); return r; }
  bool has_tag(Tag t) const { return (_tag_set & bit(t)) != 0; }
private:
  static uintptr_t bit(Tag t) { return uintptr_t(1) << t; }
  uintptr_t _tag_set;
};

class Shape {
public:
  enum Kind { kPolygon, kRectangle, kSquare, kCircle };
  bool is_a(Kind k) const { return _rtti.has_tag(k); }
  virtual ~Shape() {}
  virtual double area() const = 0;
protected:
  typedef FakeRtti<Kind> Rtti;
  explicit Shape(Rtti r) : _rtti(r) {}
private:
  Rtti _rtti;
};

class Polygon : public Shape {
protected:
  explicit Polygon(Rtti r) : Shape(r.add_tag(kPolygon)) {}   // add my bit, pass it up
};

class Square : public Rectangle {                            // Rectangle and Circle follow the same pattern
public:
  static const Kind kind = kSquare;
  explicit Square(double s) : Rectangle(Rtti(kSquare), s, s) {}
};

template<typename T> T* shape_cast(Shape* s) {
  assert(s->is_a(T::kind) && "wrong kind of shape");        // compiled out under NDEBUG
  return static_cast<T*>(s);
}
```

A `Square` ends up with the Polygon, Rectangle and Square bits set; a `Circle` has only its own. Here are three ways to turn a `Shape*` into a `Rectangle*`, from a release build compiled `-frtti` so that all three are available side by side ([Compiler Explorer](https://godbolt.org/z/8Gnxvxq6b), full source):

```nasm
; via_shape_cast: shape_cast<Rectangle>(s), the release form
        movq    %rdi, %rax          ; return the pointer unchanged
        ret                         ; the assert is gone under NDEBUG

; via_is_a: s->is_a(Shape::kRectangle) ? static_cast<Rectangle*>(s) : nullptr
        xorl    %edx, %edx          ; edx = 0, the null result
        movq    %rdi, %rax          ; rax = s
        testb   $2, 8(%rdi)         ; bit 1 (kRectangle) of _tag_set, just past the vptr
        cmove   %rdx, %rax          ; branchless: zero rax if the bit was clear
        ret                         ; return rax

; via_dynamic_cast: dynamic_cast<Rectangle*>(s)
        testq   %rdi, %rdi          ; null in...
        je      .L6                 ; ...skip the runtime entirely
        xorl    %ecx, %ecx          ; hint = 0: no statically known offset
        movl    $typeinfo for Rectangle, %edx   ; &type_info for the target type
        movl    $typeinfo for Shape, %esi       ; &type_info for the source type
        jmp     __dynamic_cast      ; tail-call into the C++ runtime, out of line
.L6:
        xorl    %eax, %eax          ; ...null out
        ret                         ; return nullptr
```

The companion benchmark times the same forms on a hierarchy of exactly this shape: a four-level chain plus a sibling of the second level, with the pointer always holding the most-derived class. Its classes are a clone of HotSpot's GC barrier sets, where I first met the pattern, in the positions of `Shape`, `Polygon`, `Rectangle`, `Square` and `Circle`. These numbers are GCC 11.4.0 code linked against the libstdc++ 15.2.0 runtime; the next section explains why the runtime needs naming:

| operation, on a `Square` | ns/op | vs `is_a()` |
|---|---:|---:|
| baseline, no cast | 0.48 | |
| a virtual call, for scale | 2.39 | |
| `shape_cast<Rectangle>`, release build | 0.48 | |
| `is_a(kRectangle)` | 1.91 | 1x |
| `dynamic_cast<Square*>`, exact-type hit | 5.74 | 3.0x |
| `dynamic_cast<Rectangle*>`, hit on the immediate base | 30.20 | 15.8x |
| `dynamic_cast<Circle*>`, miss | 55.72 | 29.2x |

Don't read much into the top four rows. At 2.10 GHz one cycle is 0.476 ns, and those numbers are 1, 5, 1 and 4 cycles exactly; they quantize, and they shuffle between builds for reasons unrelated to the cast. It is [the same quantization behind a phantom 20 percent swing in an earlier benchmark]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}). The honest summary is that the release-build cast, `is_a` and doing nothing sit within a couple of cycles of each other.

The `dynamic_cast` rows are far enough out to mean something, and what they mean is that its cost depends on the answer. An exact-type match takes the fast path. Casting to the immediate base costs five times that, because the runtime has to walk the hierarchy to find it, and a miss costs ten times it, because the runtime must exhaust the hierarchy before it can return null. A failing `dynamic_cast` runs 23 times a virtual call, in a codebase where [virtual dispatch itself gets scrutinised]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}). `is_a()` is flat whatever you ask it.

## What Changed Across Compilers and Versions

The front end has barely moved. The one construct that changed between versions in the acceptance test is `std::function::target<T>()`: GCC 9.5.0 and 10.4.0 reject it under `-fno-rtti`, and from GCC 11 libstdc++ keeps it working by comparing the address of the stored manager function instead of a `type_info`. The vtable layout, as shown earlier, never changed at all.

The runtime is another story. `dynamic_cast` compiles to a call into `__dynamic_cast`, which lives in the C++ runtime library, so "which compiler" is really two questions: which compiler generated the call, and which runtime answered it. [An earlier post]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %}) found that libstdc++'s version mattered more to `std::variant` than the dispatch pattern did; for `dynamic_cast`, the library that matters is the compiled runtime rather than the headers. I ran the cast benchmark with each compiler linked against its own runtime, `-static-libstdc++`, median of five runs, pinned to one core:

| compiler and runtime | `is_a` | exact hit | base hit | miss |
|---|---:|---:|---:|---:|
| GCC 9.5.0, libstdc++ 9.5.0 | 1.91 | 13.39 | 28.30 | 55.98 |
| GCC 10.4.0, libstdc++ 10.4.0 | 1.91 | 12.91 | 27.76 | 56.83 |
| GCC 11.4.0, libstdc++ 11.4.0 | 1.91 | 13.39 | 29.93 | 59.45 |
| GCC 12.4.0, libstdc++ 12.4.0 | 1.91 | 13.90 | 29.78 | 85.63 |
| GCC 13.4.0, libstdc++ 13.4.0 | 1.91 | 5.74 | 32.20 | 59.91 |
| GCC 14.3.0, libstdc++ 14.3.0 | 1.91 | 5.74 | 32.20 | 64.10 |
| GCC 15.2.0, libstdc++ 15.2.0 | 1.91 | 5.26 | 28.39 | 52.84 |
| Clang 22.1.4, libstdc++ 15.2.0 | 0.72 | 5.26 | 30.68 | 55.50 |
| Clang 22.1.4, libc++abi 22.1.4 | 0.72 | 4.78 | 12.91 | 28.48 |

The exact-type hit falls from about 13 ns to 5.74 between GCC 12 and 13, and that is a single commit. Jason Merrill's [873d395c2976](https://gcc.gnu.org/git/?p=gcc.git;a=commit;h=873d395c2976), "small dynamic_cast optimization", teaches `__dynamic_cast` to check whether the object's complete type is the target before doing anything else:

```cpp
  // Avoid virtual function call in the simple success case.
  if (src2dst >= 0
      && src2dst == -prefix->whole_object
      && *whole_type == *dst_type)
    return const_cast <void *> (whole_ptr);
```

That commit leaves the hierarchy walk alone, and the walk is what serves the base hit and the miss. I read the spread in those two columns as [code placement]({% post_url 2026-06-15-the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache %}) rather than anything the library authors did. GCC 12.4.0's 85.6 ns miss held steady across all five runs, so it is not noise, and I have not found a source change to explain it.

This is also where the first version of this post went wrong, twice. It labelled its timings GCC 11.4.0, but they were GCC 11.4.0 code running against the libstdc++ 15.2.0 runtime: conda-forge's GCC 11 environment links the newest `libstdc++.so` and sets an rpath to it, and rebuilding it that way reproduces those numbers to within 0.05 ns. It also claimed that the exact-type fast path was "genuinely machine-dependent", 7.4 times `is_a` on an AMD EPYC-Rome VM against 3.0 times on the Xeon. The EPYC build had picked up Ubuntu's system libstdc++, which comes from GCC 12 and predates the fast path. With the runtime pinned on both machines, the fast path cuts the exact hit by 2.3x on each: 13.39 to 5.74 ns on the Xeon, 7.69 to 3.29 ns on the EPYC. If you benchmark `dynamic_cast`, check which runtime your loader picked.

The bottom two rows hold Clang's code generation constant and swap the runtime, and libc++abi walks the hierarchy twice as fast. A profile shows why. The libstdc++ build spends 28.65 percent of its time in `__strcmp_avx2`, and the libc++abi build never calls `strcmp`. libstdc++ does not assume one `type_info` per type, so when two `type_info` pointers differ, [`operator==` falls back to comparing the mangled names](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/libsupc%2B%2B/typeinfo;hb=releases/gcc-15.2.0#l208). On ELF, libc++ [assumes they are unique](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.4/libcxx/include/typeinfo#L185-L187) and compares addresses. libstdc++'s choice survives shared libraries loaded with `RTLD_LOCAL` or built with hidden visibility, where the same type can end up with two `type_info` objects; libc++'s is faster and relies on that not happening.

Clang 17 went a step further. When the target of a `dynamic_cast` is a `final` class, Richard Smith's [9d525bf94b25](https://github.com/llvm/llvm-project/commit/9d525bf94b25) skips the runtime entirely and compares the object's vptr against the one vtable a `Leaf` can have ([Compiler Explorer](https://godbolt.org/z/hqa1M89xq), Clang 22.1.0 beside GCC 16.1):

```nasm
to_leaf(Base*):
        testq   %rdi, %rdi                      ; null in...
        je      .LBB0_2                         ; ...null out
        movq    %rdi, %rax                      ; tentatively return b itself
        leaq    vtable for Leaf+16(%rip), %rcx  ; the address a Leaf's vptr must hold
        cmpq    %rcx, (%rdi)                    ; is b's vptr exactly that?
        je      .LBB0_3                         ; yes: b is a Leaf
.LBB0_2:
        xorl    %eax, %eax                      ; no: return nullptr
.LBB0_3:
        retq                                    ; return rax
```

GCC 16.1 still emits the `__dynamic_cast` call. Marking the benchmark's most-derived class `final` takes Clang's exact hit from 5.26 ns to 0.96 ns, two cycles, which puts it in the same bracket as `is_a()`. The optimization rests on the same assumption as libc++: one vtable per class. `-fno-assume-unique-vtables` switches it off, and the commit message restricts it to the Itanium ABI because its author did not know "what guarantees are made about vfptr uniqueness" on Microsoft's. Hold that thought for the OpenJDK section.

The compilers also disagree about where the flag's boundary sits. MSVC's `/GR-` accepts `typeid` and `dynamic_cast` on polymorphic types with warning [C4541](https://learn.microsoft.com/en-us/cpp/error-messages/compiler-warnings/compiler-warning-level-1-c4541), "unpredictable behavior may result". Clang has a `-fno-rtti-data` mode, which is what `clang-cl /GR-` maps to, with the same philosophy: drop the data, warn, and let the code compile. It only changes anything under the Microsoft ABI, where it removes the RTTI records from vftables; on Linux it just warns. Code that builds with `/GR-` will not necessarily build with `-fno-rtti`.

## What's Coming

Three things are changing the picture, and only one of them has shipped in a standard.

Clang 18 added `-fexperimental-omit-vtable-rtti` ([f45f1c3585e6](https://github.com/llvm/llvm-project/commit/f45f1c3585e6)), which finally deletes the null slot. It is a front-end-only flag, so you pass it as `-Xclang -fexperimental-omit-vtable-rtti`, and it refuses to run without `-fno-rtti`, with an error message that reads, typo included, "call only be used with -fno-rtti". It breaks the ABI in exactly the way plain `-fno-rtti` was designed not to, which is why it is experimental. Fuchsia already uses it for its kernel.

Relative vtables go further. `-fexperimental-relative-c++-abi-vtables` stores each slot as a 32-bit offset from the vtable instead of a 64-bit absolute pointer, which halves the vtable and removes the load-time relocations entirely. Clang makes it [the default when targeting Fuchsia](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.4/clang/include/clang/Basic/TargetCXXABI.h#L67-L69). Here is what both do to the same 1800-class corpus, built with Clang 22.1.4:

| metric | `-frtti` | `-fno-rtti` | `+ omit-vtable-rtti` | `+ relative vtables` |
|---|---:|---:|---:|---:|
| file size (bytes) | 2,772,608 | 2,226,512 | 2,210,128 | 1,956,104 |
| dynamic relocations | 16,219 | 9,016 | 9,016 | 11 |
| vtable bytes | 100,856 | 100,856 | 86,448 | 50,428 |
| private-dirty after `dlopen` | 148 KB | 104 KB | 92 KB | 8 KB |
| `dlopen`, median of 400 | 256 µs | 223 µs | 198 µs | 180 µs |

Omitting the slot saves exactly 8 bytes times 1,801 vtables and not a single relocation, since a null slot never had one; the vtables pack into fewer pages, which accounts for the smaller dirty footprint. With no relocation work removed, I would not trust its faster `dlopen`. Relative vtables are the dramatic column: private-dirty memory falls from 104 KB to 8 KB because there is almost nothing left for the loader to patch. Combining the two gets the vtables down to 43,224 bytes. The catch is that the whole program must agree, C++ runtime included. Linked against an ordinary libstdc++, a relative-vtable build crashes as soon as it catches a `std::exception&` and calls `what()`. That is fine for an operating system that builds everything from source, and a non-starter for anyone else.

The third change comes from the language. C++26 adopted static reflection, [P2996](https://wg21.link/P2996), in June 2025, and GCC 16 ships it behind `-std=c++26 -freflection`; upstream Clang does not have it yet. Reflection happens at compile time and needs no typeinfo, so it works under `-fno-rtti`. On GCC 16.1 this compiles with the flag and prints the type's name:

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

It applies to the JVM only. The rest of the JDK's native code gets `TOOLCHAIN_CFLAGS_JDK`, which is `-pipe -fstack-protector` under GCC, and you can see the difference in the shipped bits: in the same JDK 17 install, `libjimage.so` carries typeinfo for `ZipDecompressor`, `ImageDecompressor` and `Endian`. The [HotSpot style guide](https://github.com/openjdk/jdk/blob/jdk-25%2B36/doc/hotspot-style.md#L471-L481) gives the rationale:

> Other than to implement exceptions (which HotSpot doesn't use), most potential uses of RTTI are better done via virtual functions. Some of the remainder can be replaced by bespoke mechanisms. The cost of the additional runtime data structures needed to support RTTI are deemed not worthwhile, given the alternatives.

The source keeps to it. There are no uses of `dynamic_cast` or `typeid` in `src/hotspot` in either JDK 17 or JDK 25. The bespoke mechanisms come in several flavours. [`FakeRttiSupport`](https://github.com/openjdk/jdk/blob/jdk-25%2B36/src/hotspot/share/utilities/fakeRttiSupport.hpp#L31-L52), the tag set the `Shape` example earlier is modelled on, arrived with [JDK-8069016](https://bugs.openjdk.org/browse/JDK-8069016) in JDK 9 for the GC's `BarrierSet` hierarchy, and its `barrier_set_cast<T>()` is the same assert-then-`static_cast` as `shape_cast`. `Klass` type checks compare a `KlassKind` field, since [JDK-8283574](https://bugs.openjdk.org/browse/JDK-8283574) in JDK 19. C2's IR nodes carry a class-id bitfield, and [`node.hpp`](https://github.com/openjdk/jdk/blob/jdk-25%2B36/src/hotspot/share/opto/node.hpp#L883-L1027) stamps out `is_X()`, `as_X()` and `isa_X()` for 132 node classes from it with a `DEFINE_CLASS_QUERY` macro. A one-bit-per-class tag set would run out at 64 classes. The class id instead spells out each node's path down the tree, a subclass extending its parent's bit pattern, so `is_X()` is a mask and a compare and all 132 classes fit in 32 bits. `CollectedHeap` exposes a `kind()` enum, and `named_heap<T>()` asserts on it before a `static_cast`.

The flag line above covers GCC and Clang, and in JDK 17 the AIX compiler gets `-qnortti -qnoeh`. The MSVC line has no `/GR-`, so HotSpot on Windows has always been built with RTTI. Nobody intended that, and in 2023 someone tried to fix it. Turning RTTI off "will reduce the binary size for Hotspot by at least 1 MB", says [JDK-8302817](https://bugs.openjdk.org/browse/JDK-8302817), and the bug exists because doing so broke the Serviceability Agent, HotSpot's out-of-process debugger. The SA identifies a HotSpot object's type by its vtable address. With RTTI on, every MSVC vftable carries a pointer to its own RTTI record and is therefore unique. With RTTI off, identical vtables were folded together, and the SA started reporting `JavaThread`s as `NotificationThread`s, a subclass that overrides no virtual function. A test turned up more duplicates, including `InstanceKlass` and `InstanceClassLoaderKlass`. David Holmes summed it up: "We have been lucky that RTTI was unintentionally left on for Windows builds, and that it did in fact cause vtables to be unique." The bug was closed as Won't Fix, on the judgement that "reducing the size of jvm.dll is not worth the risk of breaking SA", and the build change, [JDK-8303166](https://bugs.openjdk.org/browse/JDK-8303166), followed it. The style guide says only that RTTI "is disabled by the build configuration for some platforms".

Vtable uniqueness is the assumption libc++ makes about `type_info`, the assumption Clang's `final` optimization makes about vtables, and the one Microsoft's linker does not promise. Every scheme that identifies a type by an address inherits it.

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

Add it up and size is the weakest argument in the pile. Two and a half percent of a real binary, a single megabyte in OpenJDK's Windows attempt, weighed against losing `dynamic_cast`, a standard library with holes in it, sanitizers that quietly check less, and a mixing failure that links clean and crashes later. If size is all you want, `-ffunction-sections -Wl,--gc-sections` is the first thing to try, and it costs nothing socially. Relative vtables save more than `-fno-rtti` does, if you control every byte of the program.

What the flag actually buys is an invariant the compiler enforces: no run-time type introspection anywhere in this binary. The projects that use it all made that design decision first, built their own cheap, predictable type checks, and then used the flag to keep it true. That is the right order. Reach for `-fno-rtti` to ratify a decision you have already made, not to force one onto a mature codebase for two percent.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core. The cross-check machine is a 64-core AMD EPYC-Rome VM, whose figures appear only in the runtime-version discussion.
Compilers: GCC 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 with libc++ 22.1.4, all from conda-forge via micromamba. The Impact section's cast table is GCC 11.4.0 code dynamically linked against the environment's libstdc++ 15.2.0 runtime; the compiler matrix links each compiler's own runtime with `-static-libstdc++` (GCC 15.2.0 fully static, since its runtime needs a newer glibc than the host has). Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`. The alignment flags were measured against a plain `-O2 -DNDEBUG` build and changed nothing here; they are kept for consistency with the rest of the series.
Methodology: the cast microbenchmark is 30M iterations after a 2M warmup, 5 runs, median reported, with an inline asm barrier to stop the optimiser hoisting a loop-invariant cast. Size and relocation figures are static ELF accounting and exactly reproducible. Compile wall time is best of 5 at `-j64`; the compile CPU rows are user CPU time for compiling each group of files serially, median of 5. `dlopen` figures are the median of 400 loads, with private-dirty pages read from `/proc/self/smaps` after the final load. The construct matrix, compiler matrix and Clang vtable experiments are `make constructs`, `make compilers` and `make future`.
JVM figures come from a stock OpenJDK 17.0.0.1 build (`nm -a`, `readelf -rW`); `make jvm` runs the same census against any JDK you point it at. Source citations are pinned to jdk-17+35 and jdk-25+36.

Previously: [Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %})
Next: [A Week in Aurora: CppCon 2026]({% post_url 2026-09-29-a-week-in-aurora-cppcon-2026 %})
