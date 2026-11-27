---
title: "Your Stdlib Implementation Matters More Than the Dispatch Pattern"
date: 2026-05-25
categories: [C++, Performance]
tags: [dispatch, variant, virtual, stdlib, libstdc++, libc++, benchmarks, gcc]
description: >-
  std::variant went from the slowest dispatch mechanism on GCC 11 to the
  fastest on GCC 12. Nothing changed but the compiler.
image:
  path: /assets/img/og/your-stdlib-implementation-matters-more-than-the-dispatch-pattern.png
  alt: "Your Stdlib Implementation Matters More Than the Dispatch Pattern"
  hero: false
---

In a [previous post]({% post_url 2026-05-19-why-std-visit-may-be-slower-than-a-vtable %}), I showed that `std::variant + std::visit` was 28% slower than virtual dispatch on GCC 11, and traced the overhead to libstdc++'s implementation: a compile-time-generated function pointer table, a lambda capture round-trip through the stack, and an unconditional valueless check.

The analysis held for one version of one standard library. "Variant is slower than virtual" turned out to be a fact about GCC 11's libstdc++.

Then I upgraded the compiler.

## The Numbers

Here is the same benchmark source, 100M iterations on an Intel Xeon Gold 6130, built with seven GCC versions:

| Compiler | variant (ns/call) | virtual (ns/call) | Faster approach |
|----------|-------------------|--------------------|--------------------|
| GCC 9.5 | 3.95 | 2.39 | virtual (65% faster) |
| GCC 10.4 | 3.59 | 2.39 | virtual (50% faster) |
| GCC 11.4 | 3.72 | 2.39 | virtual (56% faster) |
| **GCC 12.4** | **1.44** | **2.39** | **variant (40% faster)** |
| GCC 13.4 | 1.44 | 2.39 | variant (40% faster) |
| GCC 14.3 | 1.44 | 2.39 | variant (40% faster) |
| GCC 15.2 | 1.47 | 2.42 | variant (39% faster) |

All seven versions compiled with the same flags (`-O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`) and measured on the same hardware in the same session. The alignment flags eliminate code placement artifacts that can add up to 0.48 ns of noise to indirect call benchmarks.

From GCC 11 to GCC 12, `std::visit` went from the slowest dispatch mechanism to the fastest. The variant numbers dropped from 3.72 ns to 1.44 ns, a 61% reduction. Virtual dispatch stayed at 2.39 ns across all versions, unchanged.

## What GCC 12 Did

The improvement has a name in [libstdc++ 12.4's source](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-12.4.0):

```cpp
constexpr size_t __max = 11; // "These go to eleven."
```

For single-variant visits with 11 or fewer alternatives, [GCC 12 added a switch-based fast path](https://gcc.gnu.org/git/?p=gcc.git;a=blobdiff;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-12.1.0;hpb=releases/gcc-11.4.0) in `__do_visit`. Instead of building a function pointer table and calling through it, the compiler generates a `switch` on the variant's index and inlines the visitor body directly into each case.

The switch matters less than what the optimizer then does with it, and that happens in two stages. First, `__do_visit` generates a `switch` on the variant index instead of a function pointer table:

```cpp
// What GCC 12's __do_visit effectively generates (simplified)
switch (__v.index()) {
    case 0: return __visitor(std::get<0>(__v));  // EpsilonBS
    case 1: return __visitor(std::get<1>(__v));  // SerialBS
    case 2: return __visitor(std::get<2>(__v));  // G1BS
}
```

This alone wouldn't help much; the switch still dispatches on every call. The second stage is what the optimizer does: since the variant doesn't change type inside the loop, the compiler hoists the switch above the loop and jumps directly to the matching case's loop body. The switch runs once; the loop runs 100M times with the visitor inlined. (The optimization depends on the variant staying type-stable across iterations. [Part 5]({% post_url 2026-06-02-when-dispatch-mechanism-choice-stops-mattering %}) measures what happens when it doesn't.)

Here's the GCC 9 hot loop (pre-switch, representative of GCC 9-11):

```nasm
; GCC 9 -- function pointer table dispatch
.L37:
    movzbl 23(%rsp), %eax        ; load variant discriminant
    movq   %rbp, 32(%rsp)        ; STORE 1: spill to lambda capture
    cmpb   $-1, %al              ; valueless check (can't fire, still checked)
    movq   %r13, 40(%rsp)        ; STORE 2: spill to lambda capture
    cmove  %r14, %rax            ; conditional move for valueless
    movq   %r12, %rsi            ; pass variant address
    movq   %rbx, %rdi            ; pass lambda capture address
    call   *(%r15,%rax,8)        ; indirect call through _S_vtable
    ; ... loop counter update ...
    jle    .L37
```

Nine instructions before the call. Two stack stores, a valueless check, and an indirect call through a function pointer table. The called function then reads those captures back from the stack.

Here's the GCC 12 hot loop for the same source code, same variant alternative (SerialBS, index 1):

```nasm
; GCC 12 -- switch optimization + loop hoisting
.L28:
    movq   %rdx, %rax
    andl   $63, %eax              ; i % 64
    movl   %edx, -288(%rbp,%rax,4) ; store directly into heap array
    incq   %rdx
    movl   $1, sink(%rip)         ; side effect (volatile write)
    cmpq   $100000000, %rdx
    jne    .L28
```

Seven instructions total, including the loop counter and branch. There's no call left at all; the visitor body is inlined into the loop.

The compiler checked the variant index once before entering the loop (`cmpb $1, %bl` earlier in the function), then jumped to the matching loop body. What's left is a tight loop indistinguishable from hand-written code.

The table machinery is gone from the binary too. GCC 9 generates 32 symbols related to visit dispatch (`_S_vtable`, `__gen_vtable`, `__visit_invoke` and friends); GCC 12 generates zero.

Compare that to the virtual dispatch loop, which is unchanged across all seven compiler versions:

```nasm
; Virtual dispatch -- same on GCC 9 through GCC 15
.L36:
    movq   8(%rsp), %rdi          ; load object pointer
    movq   (%rdi), %rax           ; load vptr (dependent load 1)
    movl   %ebx, %edx             ; value argument
    call   *16(%rax)              ; vtable entry (dependent load 2) + indirect call
    incq   %rbx
    cmpq   $100000000, %rbx
    jne    .L36
```

Virtual dispatch can't be optimized away the same way. The compiler can't hoist the vtable lookup out of the loop because the object pointer could, in principle, change between iterations (even though it doesn't in this benchmark). The indirect call through the vtable prevents inlining. The two dependent loads are the structural cost of the mechanism. They're the same on every GCC version because there's nothing for the compiler to improve.

## The Timeline: How Three Stdlibs Diverged

The switch optimization didn't appear everywhere at once. Each major standard library took a different path:

| Year | libstdc++ (GCC) | libc++ (Clang) | MSVC STL |
|------|-----------------|----------------|----------|
| 2017 | Function pointer table only | Function pointer table only | Graduated switches (internal, pre-open-source) |
| 2019 | + `__never_valueless` | -- | Open-sourced with switches already in place |
| 2020 | -- | Switch upstreamed, reverted twice (binary size) | -- |
| **2022** | **+ switch for <= 11 alternatives** | Still table-only | -- |
| 2025 | + C++26 member visit | Still table-only | -- |
| **2026** | -- | **LLVM JumpTableToSwitch pass (optimizer-level fix)** | -- |

**libstdc++ (GCC):** Five years of table-only dispatch (GCC 7 through 11), then a single commit in GCC 12 added the switch path with a threshold of 11 alternatives. The threshold hasn't changed since. Earlier versions weren't idle. GCC 8 added compact index types (smaller variant objects), GCC 9 added the [`_Never_valueless_alt`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-9.1.0#l370) optimization that eliminates the valueless check for trivially copyable types. None of those touched the dispatch path.

**libc++ (Clang/LLVM):** The switch optimization was [upstreamed in August 2020](https://github.com/llvm/llvm-project/commit/a175a96) based on the mpark::variant implementation, then [reverted](https://github.com/llvm/llvm-project/commit/057028e) due to internal failures at Google. It was [re-applied in October 2020](https://github.com/llvm/llvm-project/commit/35d2269), then [reverted again in November 2020](https://github.com/llvm/llvm-project/commit/9c09757bca) due to substantial binary size increases in non-optimized builds. As of [LLVM 20 (2026)](https://github.com/llvm/llvm-project/blob/llvmorg-20.1.0/libcxx/include/variant), the switch path has not been re-applied to the headers. Instead, LLVM added a mid-level optimizer pass ([JumpTableToSwitch](https://github.com/llvm/llvm-project/pull/190674), [enabled by default](https://github.com/llvm/llvm-project/commit/a80d2a719374) in April 2026) that recognizes the function-pointer-table pattern and transforms it to a switch at `-O2`. libc++ keeps its compact headers and small debug builds; the optimizer recovers the switch form. This will ship in a future Clang release.

**MSVC STL:** Had graduated switches before the STL was even [open-sourced in September 2019](https://github.com/microsoft/STL). The [visit implementation](https://github.com/microsoft/STL/blob/main/stl/inc/variant) uses `_STL_STAMP` macros to generate switch cases in powers of four: 4 cases, 16, 64, and 256. Beyond 256 total states (the product of all variant sizes for a multi-variant visit), it falls back to a function pointer table. It also flattens all variant indices into a single canonical integer biased by +1, so the valueless state maps to case 0 rather than requiring a separate check. I haven't benchmarked MSVC here (the series focuses on GCC/Linux), but the approach is the most sophisticated of the three from a design perspective.

## Michael Park's Own Benchmark

Michael Park [wrote libc++'s variant implementation](https://github.com/llvm/llvm-project/commit/0d3d8de014f1012dd0b2c2c077099cf6face44e5) in 2016. He then created the standalone [mpark::variant](https://github.com/mpark/variant) library as a C++11/14 backport, where he implemented *both* dispatch strategies (table and switch) and benchmarked them head-to-head.

His [January 2019 results](https://mpark.github.io/programming/2019/01/22/variant-visitation-v2/) were unambiguous:

| Alternatives | Table (ns) | Switch (ns) | Speedup |
|---|---|---|---|
| 5 | 10,607 | 3,815 | 2.8x |
| 15 | 12,432 | 2,950 | 4.2x |
| 32 | 10,590 | 2,927 | 3.6x |

The switch approach was 2-4x faster across the board. libstdc++ added a switch path of its own in GCC 12, three years later. The same optimization was [upstreamed to libc++ in 2020](https://github.com/llvm/llvm-project/commit/a175a96), but was reverted twice: first due to internal test failures, then due to binary size increases in debug builds. As of 2026, the switch path has not been re-applied to libc++.

Park is also the author of the C++20 `visit<R>()` overload ([P0655](https://wg21.link/P0655)) and C++26's pattern matching proposal ([P2688](https://wg21.link/P2688)), which proposes an infix `match` expression and `let` binding introducer that could replace `std::visit` entirely. So the author of libc++'s table dispatch is on both sides of this story: he measured it losing to a switch, and his pattern-matching work could make `std::visit` unnecessary.

## What's Coming

**C++26 member visit** ([P2637](https://wg21.link/P2637)): `v.visit(f)` instead of `std::visit(f, v)`. Using deducing `this`, the implementation gets more information at the call site. The variant knows its own type, which can simplify the dispatch path compared to the free function version where the variant arrives as a forwarding reference.

**Pattern matching** ([P2688](https://wg21.link/P2688)): An infix `match` expression with `let` bindings that would move dispatch from a library mechanism (where the optimizer has to reverse-engineer the intent from template metaprogramming) to a language construct (where the compiler knows exactly what's happening from the start). The entire `std::visit` implementation strategy (table vs switch, valueless checks, lambda captures) becomes irrelevant.

## Checking Your Own Toolchain

If you're on GCC 11 or earlier, `std::visit` dispatches through a function pointer table, and moving to GCC 12 or later makes the same code about 2.6x faster for small variants. On Linux, Clang can link either libc++ or libstdc++. To find out which:

```bash
echo '#include <variant>' | clang++ -xc++ -dM -E - | grep _LIBCPP
```

If that prints a define, you're on libc++ and get no switch path from the headers. If it prints nothing, you're on libstdc++, and version 12 or later has the switch. On macOS, Clang defaults to libc++. If you're stuck on an older compiler and the dispatch is hot, the [lazy resolution pattern]({% post_url 2026-05-12-lazy-resolution-resolve-once-dispatch-forever %}) from Part 2 doesn't depend on the stdlib at all.

Parts 1 and 3 of this series both reported "std::variant is slower than virtual dispatch," and that was only true through GCC 11. A benchmark that says "GCC" without a version number is incomplete, and that includes mine.

## Why the Alignment Flags?

You might have noticed `-falign-functions=64 -falign-loops=64` in the benchmark methodology. Without those flags, virtual dispatch measured anywhere from 2.39 ns to 2.87 ns depending on the GCC version, not because the compiler generated better code, but because the linker happened to place the called function across a cache line boundary in some builds. A 0.48 ns ghost that looked like a compiler improvement but was pure binary layout noise. [Part 6]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}) traces how we found it, why it matters, and what it means for every C++ microbenchmark you've ever read.

---

*All benchmarks and source code are in the [companion repository](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark). Measured on Intel Xeon Gold 6130, `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`, pinned to a single core with `taskset -c 0`. GCC versions: 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0 (all via conda-forge); 15.2 conda-forge binary, statically linked (Xeon host glibc 2.28 predates GCC 15's glibc 2.34 requirement). Best of 3 runs reported. Stdlib source: [libstdc++ 12.4 `<variant>`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-12.4.0).*

---

**Series start:** [Four Ways to Dispatch a Runtime-Selected Strategy in C++]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}). The head-to-head comparison that started this investigation.

**Previously:** [Why std::visit May Be Slower Than a Vtable]({% post_url 2026-05-19-why-std-visit-may-be-slower-than-a-vtable %}). The assembly-level explanation of where the overhead comes from on GCC 11.

**Next:** [When Dispatch Mechanism Choice Stops Mattering]({% post_url 2026-06-02-when-dispatch-mechanism-choice-stops-mattering %}). What happens when you mix multiple plugin types in the same hot loop, and which dispatch mechanism degrades most gracefully.
