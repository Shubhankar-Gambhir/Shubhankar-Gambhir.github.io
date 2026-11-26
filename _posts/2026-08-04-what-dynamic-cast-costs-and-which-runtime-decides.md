---
title: "What dynamic_cast Costs, and Which Runtime Decides"
date: 2026-08-04
last_modified_at: 2026-10-01
categories: [C++, Performance]
tags: [rtti, dynamic-cast, vtable, hotspot, gcc, clang, libstdc++, libc++, benchmarks]
description: >-
  What a checked downcast costs as a HotSpot-style tag set against dynamic_cast's exact-hit,
  base-hit and miss paths, then how dynamic_cast's cost moved across GCC 9 to 15, Clang 22 and
  libc++: one GCC 13 commit for the exact-type hit, libc++abi's address comparisons for the
  hierarchy walk, and Clang's single vptr compare for final classes.
image:
  path: /assets/img/og/what-dynamic-cast-costs-and-which-runtime-decides.png
  alt: "What dynamic_cast Costs, and Which Runtime Decides"
  hero: false
---

A `dynamic_cast` that misses costs 29 times the single bit test that HotSpot-style code uses in its place, and which C++ runtime answers the call can halve the miss itself.

[The Null Slot: What -fno-rtti Actually Removes]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %}) opened this three-part series on `-fno-rtti` by counting what the flag saves in a binary. This second post is about the type checks themselves.

## Impact: Speed

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

Don't read much into the top four rows. At 2.10 GHz one cycle is 0.476 ns, and those numbers are 1, 5, 1 and 4 cycles exactly; they quantize, and they shuffle between builds for reasons unrelated to the cast. It is [the same quantization behind a phantom 20 percent swing in an earlier benchmark]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}). In practice the release-build cast, `is_a` and doing nothing sit within a couple of cycles of each other.

The `dynamic_cast` rows are far enough out to mean something, and what they mean is that its cost depends on the answer. An exact-type match takes the fast path. Casting to the immediate base costs five times that, because the runtime has to walk the hierarchy to find it, and a miss costs ten times it, because the runtime must exhaust the hierarchy before it can return null. A failing `dynamic_cast` runs 23 times a virtual call, in a codebase where [virtual dispatch itself gets scrutinised]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}). `is_a()` is flat whatever you ask it.

## What Changed Across Compilers and Versions

The front end has barely moved. The one construct that changed between versions in [the acceptance test]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %}) (eleven one-line constructs, compiled under `-fno-rtti` with all eight compilers) is `std::function::target<T>()`: GCC 9.5.0 and 10.4.0 reject it under `-fno-rtti`, and from GCC 11 libstdc++ keeps it working by comparing the address of the stored manager function instead of a `type_info`. The vtable layout, which the same post probed on every compiler, never changed at all.

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

This is also where my first write-up of these benchmarks went wrong, twice. It labelled its timings GCC 11.4.0, but they were GCC 11.4.0 code running against the libstdc++ 15.2.0 runtime: conda-forge's GCC 11 environment links the newest `libstdc++.so` and sets an rpath to it, and rebuilding it that way reproduces those numbers to within 0.05 ns. It also claimed that the exact-type fast path was "genuinely machine-dependent", 7.4 times `is_a` on an AMD EPYC-Rome VM against 3.0 times on the Xeon. The EPYC build had picked up Ubuntu's system libstdc++, which comes from GCC 12 and predates the fast path. With the runtime pinned on both machines, the fast path cuts the exact hit by 2.3x on each: 13.39 to 5.74 ns on the Xeon, 7.69 to 3.29 ns on the EPYC. If you benchmark `dynamic_cast`, check which runtime your loader picked.

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

GCC 16.1 still emits the `__dynamic_cast` call. Marking the benchmark's most-derived class `final` takes Clang's exact hit from 5.26 ns to 0.96 ns, two cycles, which puts it in the same bracket as `is_a()`. The optimization rests on the same assumption as libc++: one vtable per class. `-fno-assume-unique-vtables` switches it off, and the commit message restricts it to the Itanium ABI because its author did not know "what guarantees are made about vfptr uniqueness" on Microsoft's. Hold that thought for [What Comes After -fno-rtti, and How OpenJDK Lives Without RTTI]({% post_url 2026-08-19-what-comes-after-fno-rtti-and-how-openjdk-lives-without-rtti %}).

The compilers also disagree about where the flag's boundary sits. MSVC's `/GR-` accepts `typeid` and `dynamic_cast` on polymorphic types with warning [C4541](https://learn.microsoft.com/en-us/cpp/error-messages/compiler-warnings/compiler-warning-level-1-c4541), "unpredictable behavior may result". Clang has a `-fno-rtti-data` mode, which is what `clang-cl /GR-` maps to, with the same philosophy: drop the data, warn, and let the code compile. It only changes anything under the Microsoft ABI, where it removes the RTTI records from vftables; on Linux it just warns. Code that builds with `/GR-` will not necessarily build with `-fno-rtti`.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core. The cross-check machine is a 64-core AMD EPYC-Rome VM, whose figures appear only in the runtime-version discussion.
Compilers: GCC 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 with libc++ 22.1.4, all from conda-forge via micromamba. The Impact section's cast table is GCC 11.4.0 code dynamically linked against the environment's libstdc++ 15.2.0 runtime; the compiler matrix links each compiler's own runtime with `-static-libstdc++` (GCC 15.2.0 fully static, since its runtime needs a newer glibc than the host has). Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`. The alignment flags were measured against a plain `-O2 -DNDEBUG` build and changed nothing here; they are kept for consistency with the rest of the series.
Methodology: the cast microbenchmark is 30M iterations after a 2M warmup, 5 runs, median reported, with an inline asm barrier to stop the optimiser hoisting a loop-invariant cast. The compiler matrix is `make compilers`.

**Previously:** [The Null Slot: What -fno-rtti Actually Removes]({% post_url 2026-07-21-the-null-slot-what-fno-rtti-actually-removes %})

**Next:** [What Comes After -fno-rtti, and How OpenJDK Lives Without RTTI]({% post_url 2026-08-19-what-comes-after-fno-rtti-and-how-openjdk-lives-without-rtti %})
