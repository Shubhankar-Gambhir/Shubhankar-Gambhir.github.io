---
title: "Which Runtime Answers dynamic_cast, and What Comes After -fno-rtti"
date: 2026-08-28
categories: [C++, Performance]
tags: [rtti, dynamic-cast, vtable, abi, gcc, clang, llvm, libstdc++, libc++, reflection, relocations, benchmarks]
description: >-
  How dynamic_cast's cost moved across GCC 9 to 15, Clang 22 and libc++: one GCC 13 commit
  for the exact-type hit, libc++abi's address comparisons for the hierarchy walk, and Clang's
  single vptr compare for final classes. Then Clang's experimental vtable flags, which go past
  -fno-rtti, and what C++26 static reflection can and cannot replace.
image:
  path: /assets/img/og/which-runtime-answers-dynamic-cast-and-what-comes-after-fno-rtti.png
  alt: "Which Runtime Answers dynamic_cast, and What Comes After -fno-rtti"
  hero: false
---

Keep Clang's code generation fixed, swap the libstdc++ runtime for libc++abi, and a `dynamic_cast` that misses gets twice as fast; none of seven GCC releases, each linked against its own runtime, comes close. The benchmark is the one from [What -fno-rtti Saves, and What dynamic_cast Costs]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}), the second of these four posts on `-fno-rtti`. It takes a `Shape*` that always points at a `Square`, in a five-class hierarchy where `Square` derives from `Rectangle`, `Rectangle` from `Polygon`, and `Polygon` and `Circle` from `Shape`. A `dynamic_cast` to `Square*` is an exact-type hit, to `Rectangle*` a hit on the immediate base, and to `Circle*` a miss. Against those sits `is_a()`, a single bit test on a tag set each object carries, the kind of type tag that codebases built with `-fno-rtti` use instead.

## What Changed Across Compilers and Versions

The front end has barely moved. The one construct that changed between versions in [the acceptance test]({% post_url 2026-08-26-the-null-slot-what-fno-rtti-actually-removes %}) (eleven one-line constructs, compiled under `-fno-rtti` with all eight compilers) is `std::function::target<T>()`: GCC 9.5.0 and 10.4.0 reject it under `-fno-rtti`, and from GCC 11 libstdc++ keeps it working by comparing the address of the stored manager function instead of a `type_info`. The vtable layout, which the same post probed on every compiler, never changed at all.

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

GCC 16.1 still emits the `__dynamic_cast` call. Marking the benchmark's most-derived class `final` takes Clang's exact hit from 5.26 ns to 0.96 ns, two cycles, which puts it in the same bracket as `is_a()`. The optimization rests on the same assumption as libc++: one vtable per class. `-fno-assume-unique-vtables` switches it off, and the commit message restricts it to the Itanium ABI because its author did not know "what guarantees are made about vfptr uniqueness" on Microsoft's. Hold that thought for [How OpenJDK and Everyone Else Live Without RTTI]({% post_url 2026-08-29-how-openjdk-and-everyone-else-live-without-rtti %}).

The compilers also disagree about where the flag's boundary sits. MSVC's `/GR-` accepts `typeid` and `dynamic_cast` on polymorphic types with warning [C4541](https://learn.microsoft.com/en-us/cpp/error-messages/compiler-warnings/compiler-warning-level-1-c4541), "unpredictable behavior may result". Clang has a `-fno-rtti-data` mode, which is what `clang-cl /GR-` maps to, with the same philosophy: drop the data, warn, and let the code compile. It only changes anything under the Microsoft ABI, where it removes the RTTI records from vftables; on Linux it just warns. Code that builds with `/GR-` will not necessarily build with `-fno-rtti`.

## What's Coming

Clang 18 added `-fexperimental-omit-vtable-rtti` ([f45f1c3585e6](https://github.com/llvm/llvm-project/commit/f45f1c3585e6)), which finally deletes the null slot. It is a front-end-only flag, so you pass it as `-Xclang -fexperimental-omit-vtable-rtti`, and it refuses to run without `-fno-rtti`, with an error message that reads, typo included, "call only be used with -fno-rtti". It breaks the ABI in exactly the way plain `-fno-rtti` was designed not to, which is why it is experimental. Fuchsia already uses it for its kernel.

Relative vtables go further. `-fexperimental-relative-c++-abi-vtables` stores each slot as a 32-bit offset from the vtable instead of a 64-bit absolute pointer, which halves the vtable and removes the load-time relocations entirely. Clang makes it [the default when targeting Fuchsia](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.4/clang/include/clang/Basic/TargetCXXABI.h#L67-L69). Here is what both do to the 1800-class corpus [behind the size measurements]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %}), built with Clang 22.1.4:

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

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core. The cross-check machine is a 64-core AMD EPYC-Rome VM, whose figures appear only in the runtime-version discussion.
Compilers: GCC 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 with libc++ 22.1.4, all from conda-forge via micromamba. The compiler matrix links each compiler's own runtime with `-static-libstdc++` (GCC 15.2.0 fully static, since its runtime needs a newer glibc than the host has). Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`. The alignment flags were measured against a plain `-O2 -DNDEBUG` build and changed nothing here; they are kept for consistency with the rest of the series.
Methodology: the cast microbenchmark is 30M iterations after a 2M warmup, 5 runs, median reported, with an inline asm barrier to stop the optimiser hoisting a loop-invariant cast. Size and relocation figures are static ELF accounting and exactly reproducible. `dlopen` figures are the median of 400 loads, with private-dirty pages read from `/proc/self/smaps` after the final load. The compiler matrix and Clang vtable experiments are `make compilers` and `make future`.

**Previously:** [What -fno-rtti Saves, and What dynamic_cast Costs]({% post_url 2026-08-27-what-fno-rtti-saves-and-what-dynamic-cast-costs %})

**Next:** [How OpenJDK and Everyone Else Live Without RTTI]({% post_url 2026-08-29-how-openjdk-and-everyone-else-live-without-rtti %})
