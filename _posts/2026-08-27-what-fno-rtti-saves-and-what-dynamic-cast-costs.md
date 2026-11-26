---
title: "What -fno-rtti Saves, and What dynamic_cast Costs"
date: 2026-08-27
categories: [C++, Performance]
tags: [rtti, dynamic-cast, vtable, openjdk, hotspot, gcc, libstdc++, elf, relocations, benchmarks]
description: >-
  What -fno-rtti is worth on a 1,800-class synthetic library and on a stock OpenJDK libjvm.so,
  counted per class in name strings, typeinfo objects and dynamic relocations; why its
  build-time reputation belongs to -fno-exceptions; and what a checked downcast costs as a
  HotSpot-style tag set against dynamic_cast's hit, base-hit and miss paths.
image:
  path: /assets/img/og/what-fno-rtti-saves-and-what-dynamic-cast-costs.png
  alt: "What -fno-rtti Saves, and What dynamic_cast Costs"
  hero: false
---

On a synthetic library of 1,800 polymorphic classes, `-fno-rtti` takes 21 percent off the file. On a stock OpenJDK `libjvm.so`, the same per-class savings come to about two and a half.

The first of these four posts on the flag, [The Null Slot: What -fno-rtti Actually Removes]({% post_url 2026-08-26-the-null-slot-what-fno-rtti-actually-removes %}), covers what it deletes to get there: a `type_info` object and a mangled name string for every polymorphic class, plus the relocations that come with them. Each vtable keeps its typeinfo slot and leaves it null, so the layout, and every virtual call, stays exactly as it was. What changes is everything that used to ask the typeinfo a question, `dynamic_cast` most of all, and the second half of this post prices what projects put in its place.

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

The companion benchmark times the same forms on a hierarchy of exactly this shape: a four-level chain plus a sibling of the second level, with the pointer always holding the most-derived class. Its classes are a clone of HotSpot's GC barrier sets, where I first met the pattern, in the positions of `Shape`, `Polygon`, `Rectangle`, `Square` and `Circle`. These numbers are GCC 11.4.0 code linked against the libstdc++ 15.2.0 runtime; [the next post]({% post_url 2026-08-28-which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti %}) explains why the runtime needs naming:

| operation, on a `Square` | ns/op | vs `is_a()` |
|---|---:|---:|
| baseline, no cast | 0.48 | |
| a virtual call, for scale | 2.39 | |
| `shape_cast<Rectangle>`, release build | 0.48 | |
| `is_a(kRectangle)` | 1.91 | 1x |
| `dynamic_cast<Square*>`, exact-type hit | 5.74 | 3.0x |
| `dynamic_cast<Rectangle*>`, hit on the immediate base | 30.20 | 15.8x |
| `dynamic_cast<Circle*>`, miss | 55.72 | 29.2x |

Don't read much into the top four rows. At 2.10 GHz one cycle is 0.476 ns, and those numbers are 1, 5, 1 and 4 cycles exactly; they quantize, and they shuffle between builds for reasons unrelated to the cast. It is [the same quantization behind a phantom 20 percent swing in an earlier benchmark]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}). In practice the release-build cast, `is_a` and doing nothing sit within a couple of cycles of each other.

The `dynamic_cast` rows are far enough out to mean something, and what they mean is that its cost depends on the answer. An exact-type match takes the fast path. Casting to the immediate base costs five times that, because the runtime has to walk the hierarchy to find it, and a miss costs ten times it, because the runtime must exhaust the hierarchy before it can return null. A failing `dynamic_cast` runs 23 times a virtual call, in a codebase where [virtual dispatch itself gets scrutinised]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}). `is_a()` is flat whatever you ask it.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core.
Compilers: GCC 11.4.0 from conda-forge via micromamba. The cast table is GCC 11.4.0 code dynamically linked against the environment's libstdc++ 15.2.0 runtime. Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`. The alignment flags were measured against a plain `-O2 -DNDEBUG` build and changed nothing here; they are kept for consistency with the rest of the series.
Methodology: the cast microbenchmark is 30M iterations after a 2M warmup, 5 runs, median reported, with an inline asm barrier to stop the optimiser hoisting a loop-invariant cast. Size and relocation figures are static ELF accounting and exactly reproducible. Compile wall time is best of 5 at `-j64`; the compile CPU rows are user CPU time for compiling each group of files serially, median of 5. `dlopen` figures are the median of 400 loads, with private-dirty pages read from `/proc/self/smaps` after the final load.
JVM figures come from a stock OpenJDK 17.0.0.1 build (`nm -a`, `readelf -rW`); `make jvm` runs the same census against any JDK you point it at.

**Previously:** [The Null Slot: What -fno-rtti Actually Removes]({% post_url 2026-08-26-the-null-slot-what-fno-rtti-actually-removes %})

**Next:** [Which Runtime Answers dynamic_cast, and What Comes After -fno-rtti]({% post_url 2026-08-28-which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti %})
