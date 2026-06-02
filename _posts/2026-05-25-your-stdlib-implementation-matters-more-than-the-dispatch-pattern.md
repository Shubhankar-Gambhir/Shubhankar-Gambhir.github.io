---
title: "Your Stdlib Implementation Matters More Than the Dispatch Pattern"
date: 2026-05-25
categories: [C++, Performance]
tags: [dispatch, variant, virtual, stdlib, libstdc++, libc++, benchmarks, gcc]
description: >-
  Same source code, same hardware, same -O2. GCC 11: std::variant is 28%
  slower than virtual dispatch. GCC 12: variant is 50% faster. The stdlib
  changed the answer.
---

In a [previous post]({% post_url 2026-05-19-why-std-visit-may-be-slower-than-a-vtable %}), I showed that `std::variant + std::visit` was 28% slower than virtual dispatch on GCC 11, and traced the overhead to libstdc++'s implementation: a compile-time-generated function pointer table, a lambda capture round-trip through the stack, and an unconditional valueless check.

That analysis was correct. It was also specific to one version of one standard library. The conclusion -- "variant is slower than virtual" -- was a property of the implementation, not the abstraction.

Then I upgraded the compiler.

## The Numbers

Same source code. Same hardware (Intel Xeon Gold 6130). Same `-O2 -march=skylake-avx512`. Same 100M iterations. Different compiler version.

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

From GCC 11 to GCC 12, `std::visit` went from the slowest dispatch mechanism to the fastest. The variant numbers dropped from 3.72 ns to 1.44 ns -- a 61% reduction. Virtual dispatch stayed at 2.39 ns across all versions -- no change.

You didn't change your code. You didn't change your algorithm. You changed your standard library.

## What GCC 12 Actually Did

The improvement has a name in [libstdc++ 12.4's source](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-12.4.0):

```cpp
constexpr size_t __max = 11; // "These go to eleven."
```

For single-variant visits with 11 or fewer alternatives, [GCC 12 added a switch-based fast path](https://gcc.gnu.org/git/?p=gcc.git;a=blobdiff;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-12.1.0;hpb=releases/gcc-11.4.0) in `__do_visit`. Instead of building a function pointer table and calling through it, the compiler generates a `switch` on the variant's index and inlines the visitor body directly into each case.

But the real win isn't the switch itself. It's what the optimizer does with it.

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

Seven instructions total, **including the loop counter and branch**. No `call`. No function pointer. No lambda capture. No valueless check. The visitor body is fully inlined.

The compiler checked the variant index **once** before entering the loop (`cmpb $1, %bl` earlier in the function), then jumped to the matching loop body. Since the variant doesn't change type during the loop, the switch is hoisted out entirely. What's left is a tight loop indistinguishable from hand-written code.

The function pointer table (`_S_vtable`, `__gen_vtable`, `__visit_invoke`) doesn't just get optimized. In GCC 12's output, those symbols don't exist at all. GCC 9 generates 32 symbols related to the visit dispatch machinery. GCC 12 generates zero.

Compare that to the virtual dispatch loop, which is **unchanged** across all six compiler versions:

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

Virtual dispatch can't be optimized away the same way. The compiler can't hoist the vtable lookup out of the loop because the object pointer could, in principle, change between iterations (even though it doesn't in this benchmark). The indirect call through the vtable prevents inlining. The two dependent loads are the structural cost of the mechanism -- they're the same on every GCC version because there's nothing for the compiler to improve.

This is why the GCC 12 switch optimization inverted the result. It didn't make the indirect call faster. It eliminated the indirect call entirely, replacing library-level dispatch with compiler-level control flow that the optimizer can see through.

## The Timeline: How Three Stdlibs Diverged

The switch optimization didn't appear everywhere at once. Each major standard library took a different path:

| Year | libstdc++ (GCC) | libc++ (Clang) | MSVC STL |
|------|-----------------|----------------|----------|
| 2017 | Function pointer table only | Function pointer table only | Graduated switches (internal, pre-open-source) |
| 2019 | + `__never_valueless` | -- | Open-sourced with switches already in place |
| **2022** | **+ switch for <= 11 alternatives** | -- | -- |
| 2025 | + C++26 member visit | Still table-only | -- |

**libstdc++ (GCC):** Five years of table-only dispatch (GCC 7 through 11), then a single commit in GCC 12 added the switch path with a threshold of 11 alternatives. The threshold hasn't changed since. Earlier versions weren't idle -- GCC 8 added compact index types (smaller variant objects), GCC 9 added the [`_Never_valueless_alt`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-9.1.0#l370) optimization that eliminates the valueless check for trivially copyable types. But none of those touched the dispatch path. GCC 12 was the inflection point.

**libc++ (Clang/LLVM):** Has never added a switch fast path. From [LLVM 5 (2017)](https://github.com/llvm/llvm-project/commit/6169a59c51) to [LLVM 20 (2026)](https://github.com/llvm/llvm-project/blob/llvmorg-20.1.0/libcxx/include/variant), the dispatch strategy is unchanged: `__make_fmatrix` builds a nested array of function pointers (`__farray`), and every visit call goes through an indirect call that the optimizer cannot see through. Nine years, no switch optimization. On macOS, where Clang defaults to libc++, this is the dispatch path your code uses unless you explicitly switch to libstdc++.

**MSVC STL:** Had graduated switches before the STL was even [open-sourced in September 2019](https://github.com/microsoft/STL). The implementation uses `_STL_STAMP` macros to generate switch cases in powers of four: 4 cases, 16, 64, and 256. Beyond 256 total states (the product of all variant sizes for a multi-variant visit), it falls back to a function pointer table. It also flattens all variant indices into a single canonical integer biased by +1, so the valueless state maps to case 0 rather than requiring a separate check. The most sophisticated strategy from day one.

## The Michael Park Paradox

Michael Park [wrote libc++'s variant implementation](https://github.com/llvm/llvm-project/commit/6169a59c51) in 2016. He then created the standalone [mpark::variant](https://github.com/mpark/variant) library as a C++11/14 backport, where he implemented *both* dispatch strategies -- table and switch -- and benchmarked them head-to-head.

His [January 2019 results](https://mpark.github.io/programming/2019/01/22/variant-visitation-v2/) were unambiguous:

| Alternatives | Table (ns) | Switch (ns) | Speedup |
|---|---|---|---|
| 5 | 10,607 | 3,815 | 2.8x |
| 15 | 12,432 | 2,950 | 4.2x |
| 32 | 10,590 | 2,927 | 3.6x |

The switch approach was 2-4x faster across the board. This directly influenced libstdc++'s decision to add a switch path in GCC 12. The optimization he proved was necessary in his own library was never backported to libc++, the standard library he originally authored.

Park is also the author of the C++20 `visit<R>()` overload ([P0655](https://wg21.link/P0655)) and C++26's pattern matching proposal ([P2688](https://wg21.link/P2688)), which would give C++ a language-level `inspect` expression that could replace `std::visit` entirely. His influence flows in both directions: library implementer and language designer. The person who built the slow path, proved it was slow, and is now working to make the entire question obsolete.

## What's Coming

**C++26 member visit** ([P2637](https://wg21.link/P2637)): `v.visit(f)` instead of `std::visit(f, v)`. Using deducing `this`, the implementation gets more information at the call site. The variant knows its own type, which can simplify the dispatch path compared to the free function version where the variant arrives as a forwarding reference.

**Pattern matching** ([P2688](https://wg21.link/P2688)): A language-level `inspect` expression that would let the compiler handle dispatch natively:

```cpp
// Hypothetical C++26 pattern matching (P2688)
inspect (bs) {
    <EpsilonBS> e => e.store(addr, value);
    <SerialBS>  s => s.store(addr, value);
    <G1BS>      g => g.store(addr, value);
};
```

If adopted, this moves dispatch from a library mechanism (where the optimizer has to reverse-engineer the intent from template metaprogramming) to a language construct (where the compiler knows exactly what's happening from the start). The entire `std::visit` implementation strategy -- table vs switch, valueless checks, lambda captures -- becomes irrelevant.

The dispatch problem doesn't disappear. It moves from library to language. But that's exactly where it belongs -- the compiler has always been better at generating dispatch code than the library.

## What This Means for You

1. **Check your GCC version.** If you're on GCC 11 or earlier, `std::visit` uses a function pointer table for dispatch. Upgrade to GCC 12+ and the same code runs 2.6x faster for small variants.

2. **Know your stdlib.** On Linux, Clang can use either libc++ or libstdc++. If you're using Clang with libstdc++, you get the switch optimization (if the libstdc++ version is >= 12). If you're using Clang with libc++, you don't -- and won't until libc++ adds one.

3. **Pin your compiler in benchmarks.** "std::variant is slower than virtual dispatch" was true on GCC 11 and false on GCC 12. Any benchmark that says "GCC" without a version number is incomplete.

4. **For hot-path dispatch, measure your toolchain.** If you can't upgrade your compiler and you're dispatching millions of times per second, consider the [lazy resolution pattern]({% post_url 2026-05-12-lazy-resolution-resolve-once-dispatch-forever %}) from Part 2 -- it's compiler-independent and matches raw function pointer performance on any version.

---

*All benchmarks and source code are in the [companion repository](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark). Measured on Intel Xeon Gold 6130, `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`, pinned to a single core with `taskset -c 0`. GCC versions: 9.5.0, 10.4.0, 11.4.0, 12.4.0, 13.4.0, 14.3.0 (all via conda-forge); 15.2 conda-forge binary, statically linked (Xeon host glibc 2.28 predates GCC 15's glibc 2.34 requirement). Best of 3 runs reported. Stdlib source: [libstdc++ 12.4 `<variant>`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-12.4.0).*

---

**Previously:** [Why std::visit May Be Slower Than a Vtable]({% post_url 2026-05-19-why-std-visit-may-be-slower-than-a-vtable %}) -- the assembly-level explanation of where the overhead comes from on GCC 11.

**Series start:** [Four Ways to Dispatch a Runtime-Selected Strategy in C++]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}) -- the head-to-head comparison that started this investigation.

## Why the Alignment Flags?

You might have noticed `-falign-functions=64 -falign-loops=64` in the benchmark methodology. Without those flags, virtual dispatch measured anywhere from 2.39 ns to 2.87 ns depending on the GCC version -- not because the compiler generated better code, but because the linker happened to place the called function across a cache line boundary in some builds. A 0.48 ns ghost that looked like a compiler improvement but was pure binary layout noise. In the next post, we'll trace how we found it, why it matters, and what it means for every C++ microbenchmark you've ever read.
