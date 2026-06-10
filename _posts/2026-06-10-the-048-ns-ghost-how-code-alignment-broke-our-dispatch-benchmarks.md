---
title: "The 0.48 ns Ghost: How Code Alignment Broke Our Dispatch Benchmarks"
date: 2026-06-10
categories: [C++, Performance]
tags: [dispatch, alignment, benchmarks, perf-stat, cache-line, dsb, microbenchmark]
description: >-
  Virtual dispatch measured 2.87 ns on GCC 11 and 2.39 ns on GCC 13. Same source,
  same flags, same hardware. The investigation that traced a phantom 20% swing
  to code alignment artifacts, and what it means for every C++ microbenchmark you've
  trusted.
---

In [Part 4]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %}), I reported that virtual dispatch measured 2.87 ns on GCC 11 and 2.39 ns on GCC 13 without alignment flags, and promised a separate investigation. Same source code, same optimization flags, same hardware. A 20% performance swing between compiler versions with no visible codegen explanation.

This is that investigation.

## Reproducing the Ghost

The first step was a systematic matrix: four dispatch mechanisms across three GCC versions and three alignment settings, for thirty-six combinations in total. Each cell was measured best-of-3 on a single Xeon Gold 6130 core.

| Mechanism | GCC | ns/call |
|-----------|-----|---------|
| virtual   | 11  | 2.87    |
| virtual   | 13  | 2.39    |
| virtual   | 15  | 2.87    |
| fnptr     | 11  | 2.39    |
| fnptr     | 13  | 3.35    |
| fnptr     | 15  | 3.35    |
| variant   | 11  | 3.62    |
| variant   | 13  | 1.44    |
| variant   | 15  | 1.44    |
| crtp      | 11  | 2.87    |
| crtp      | 13  | 3.35    |
| crtp      | 15  | 2.39    |

Stare at it for a moment.

Function pointer on GCC 13: 3.35 ns. On GCC 11: 2.39 ns. Same source, same `-O2`, same hardware. CRTP shows the same reversal. Virtual dispatch flips the other direction: 2.87 ns on GCC 11, 2.39 ns on GCC 13. GCC 15 matches GCC 11 for virtual but matches GCC 13 for variant.

Nothing here follows a consistent story. GCC 13 isn't uniformly faster or slower. The same compiler version that cuts virtual dispatch time by 17% inflates function pointer dispatch by 40%.

The variant row is different. 3.62 ns on GCC 11, 1.44 ns on GCC 13 and GCC 15. That 60% improvement is real: it comes from the switch-based `std::visit` optimization in GCC 12, which I covered in [Part 4]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %}). For the other three mechanisms, the numbers jump around with no pattern that maps to assembly changes.

### Where the Functions Actually Land

To confirm the mechanism, I dumped the function addresses from the default and aligned binaries. Here's GCC 13:

| Binary | `G1BS::store` | `SerialBS::store` | `EpsilonBS::store` |
|--------|---------------|-------------------|--------------------|
| Default | 0x1660 (offset 32) | 0x1670 (offset 48) | 0x1690 (offset 16) |
| align=64 | 0x16c0 (offset 0) | 0x1700 (offset 0) | 0x1740 (offset 0) |

The "offset" is the position within the 64-byte cache line. With default alignment, `G1BS::store` starts at byte 32 of its cache line, right at the midpoint. With `-falign-functions=64`, every function starts at byte 0. The compiler inserted NOP padding before each function to push the entry point to a 64-byte boundary. Those NOPs are never executed; they just take up dead space so the function starts at a clean address.

This is the smoking gun before we even touch perf counters.

## Finding the Culprit

The clearest swings in the matrix are function pointer and CRTP dispatch on GCC 13: 3.35 ns at default alignment, 2.39 ns with `-falign-functions=64`. A 0.96 ns difference on functions that compile to a handful of instructions each. When the hot path is that small, a misaligned entry forces the front-end to fetch and decode across boundaries on every single call, and there's no instruction footprint to amortize the penalty over.

Virtual dispatch shows a smaller version of the same effect: 2.87 ns default vs. 2.40 ns aligned, a 0.47 ns gap. Its function body is large enough that the entry-point misalignment matters less proportionally. That makes virtual a less dramatic demonstration, but it's the case I had time to instrument with `perf stat`, so the rest of this section is about the subtler half of the data.

I ran `perf stat` with Intel PMU events on three specimens: gcc13 with default alignment (the ghost), gcc13 with align64 (the fix), and gcc11 with default alignment (for comparison).

| Specimen | DSB uops | MITE uops | DSB miss | L1i miss | ITLB walk | ns/call |
|----------|----------|-----------|----------|----------|-----------|---------|
| virtual gcc13 default | 1,823M | 2.6M | 155K | 138K | 806 | 2.41 |
| virtual gcc13 align64 | 1,826M | 2.6M | 157K | 120K | 630 | 2.41 |
| virtual gcc11 default | 1,824M | 2.6M | 175K | 132K | 586 | 2.89 |

The DSB (decoded stream buffer, Intel's micro-op cache; see the Intel 64 and IA-32 Architectures Optimization Reference Manual, Section 3.4.2.5) delivers roughly the same number of uops in all three cases. The MITE legacy decoder handles 2.6M uops across the board. DSB miss counts cluster around 155-175K: the slow GCC 11 specimen has about 20K more DSB misses than the GCC 13 specimens, a 13% increase that correlates with its slower wallclock time but isn't large enough on its own to explain the gap.

The L1i miss column tells a similar story. The aligned build has 120K instruction cache misses versus 138K for the default build, about a 13% reduction. The GCC 11 specimen sits in the middle at 132K. No single counter dominates; the slowdown is distributed across several front-end stages.

That distribution is itself useful information. When `perf stat` doesn't surface a single dominant counter, the slowdown is the sum of small front-end costs (extra fetch cycles, DSB fragmentation, occasional decoder fallback) rather than one bottleneck. The fix is the same regardless of which stage gets the largest share of the blame.

## What's Actually Happening

### Cache Line Straddling

x86 processors load instructions from L1i in 64-byte cache lines, but Skylake's instruction fetch unit reads at most 16 aligned bytes per cycle from the line. When a function entry sits near the end of a cache line, the IFU may need to issue two fetch cycles to gather the first 32 bytes of the function: one for the bytes still in the current line, one for the start of the next. The cache line load itself is usually warm, but the extra fetch cycle is real and it compounds with the DSB hazard described below. The address table above shows `G1BS::store` at offset 32 in the default build, sitting right at the midpoint, so roughly half the function's prologue lives in the next cache line.

### DSB Window Misalignment

Intel's decoded stream buffer caches decoded micro-ops in 32-byte aligned windows (Intel Optimization Reference Manual, Section 3.4.2.5). Each window holds up to 6 uops from a contiguous 32-byte region of instruction bytes. A function starting at, say, offset 0x18 within a 32-byte window has only 8 bytes of usable window space before the boundary. The DSB caches the decoded uops for those 8 bytes in one window entry and the rest in the next. If the DSB can't serve a window (because it's fragmented or evicted), the processor falls back to the MITE legacy decoder, which delivers roughly 4 uops per cycle instead of up to 6.

For the function pointer benchmark, `store()` is small enough that the entire hot path fits in one or two DSB windows when aligned, but straddles three when misaligned. The function is so short that the misalignment penalty has almost no instruction footprint to amortize over. That's why fnptr and CRTP show a 0.96 ns swing while virtual (with its larger function body) shows only 0.47 ns.

### The Two-Effect Confound

The virtual dispatch ghost between GCC 11 (2.87 ns) and GCC 13 (2.39 ns) is particularly tricky because it's two effects stacked on top of each other.

GCC 13 keeps the object pointer in a callee-saved register across the loop. GCC 11 reloads it from the stack on every iteration. From `objdump -d` of the hot loop:

```nasm
; GCC 11 (per-iteration reload)
  mov    0x8(%rsp),%rdi          ; reload object pointer from stack
  mov    (%rdi),%rax              ; load vtable pointer
  callq  *(%rax)                  ; indirect call

; GCC 13 (object pointer hoisted)
  mov    %r12,%rdi                ; object pointer in callee-saved r12
  mov    (%rdi),%rax              ; load vtable pointer
  callq  *(%rax)                  ; indirect call
```

That's a real codegen improvement: one fewer memory access per call. But the alignment data shows the codegen improvement accounts for almost nothing of the measured timing gap. With `-falign-functions=64`, GCC 11 measures 2.40 ns and GCC 13 measures 2.39 ns. The 0.01 ns difference is within measurement noise. The remaining 0.47 ns gap (2.87 minus 2.40) is pure alignment artifact.

Without controlling alignment, the codegen improvement and the alignment artifact look like one big 0.48 ns win. You'd conclude "GCC 13 generates better virtual dispatch code" and be wrong, or at least only 2% right. The alignment fix contributes 98% of the measured improvement. The controlled experiment is what separates the two.

The full source for these benchmarks is in the [companion repo](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark); paste `bench_virtual.cpp` and the barrier headers into Compiler Explorer to inspect the assembly for any GCC version.

Here's the visual version of good versus bad function placement:

```
    64-byte cache line
    |<----------- 0x000 to 0x03F ----------->|
    |  DSB window 0   |  DSB window 1        |
    |  0x000 - 0x01F  |  0x020 - 0x03F       |
    |                 |                       |

    GOOD: fn() at 0x004
    [====fn body (28B)====]
    Fits entirely in DSB window 0. One cache line fetch.

    BAD: fn() at 0x038
    [==8B==]|
            |[====rest of fn (20B)====........]
    Cache line boundary at 0x040 splits the function.
    Two cache line fetches. Two DSB windows touched.
```

The good placement keeps the entire function in a single cache line and a single DSB window. The bad placement forces the processor to fetch two cache lines and decode across two DSB windows, even though the function is the same size.

## The Fix and the Flag Matrix

GCC provides four alignment flags:

| Flag | What it does | GCC default | For microbenchmarks |
|------|-------------|-------------|---------------------|
| `-falign-functions=N` | Pad function entry to N-byte boundary | 16 | 64 |
| `-falign-loops=N` | Pad loop headers to N-byte boundary | 16 | 64 |
| `-falign-jumps=N` | Pad jump targets | 8 | usually unnecessary |
| `-falign-labels=N` | Pad all labels | 4 | usually unnecessary |

The defaults shown are for `-march=skylake-avx512`. GCC's generic defaults are lower.

Here's what adding those flags does to the numbers:

| Mechanism | GCC | Default | align=32 | align=64 |
|-----------|-----|---------|----------|----------|
| virtual   | 11  | 2.87    | 2.42     | 2.40     |
| virtual   | 13  | 2.39    | 2.39     | 2.39     |
| virtual   | 15  | 2.87    | 2.39     | 2.39     |
| fnptr     | 11  | 2.39    | 2.39     | 2.39     |
| fnptr     | 13  | 3.35    | 2.39     | 2.39     |
| fnptr     | 15  | 3.35    | 2.39     | 2.39     |
| variant   | 11  | 3.62    | 3.63     | 3.59     |
| variant   | 13  | 1.44    | 1.44     | 1.44     |
| variant   | 15  | 1.44    | 1.44     | 1.44     |
| crtp      | 11  | 2.87    | 2.39     | 2.39     |
| crtp      | 13  | 3.35    | 2.39     | 2.39     |
| crtp      | 15  | 2.39    | 2.39     | 2.39     |

Every non-variant mechanism converges to 2.39-2.40 ns regardless of GCC version. The 20% difference between GCC 11 and GCC 13 for virtual dispatch evaporates. The 40% swing for function pointer disappears. It was never the optimizer. It was the linker placing functions at different addresses.

Why 64 instead of 32? Because 32-byte alignment fixes DSB window issues but doesn't guarantee cache line alignment. The data confirms this: `-falign-functions=32` brought function pointer GCC 13 from 3.35 ns all the way down to 2.39 ns (matching align=64), but for virtual GCC 11, align=32 gave 2.42 ns while align=64 gave 2.40 ns. The 0.02 ns difference is small but consistent across runs, suggesting the cache line straddling fix provides a marginal improvement on top of DSB alignment for virtual dispatch's larger function body.

### What the Padding Looks Like

With `-falign-functions=64`, the compiler inserts a NOP sled before each function entry. From `objdump -d` on the aligned binary:

```nasm
; ... end of previous function ...
  ret
  nop                          ; }
  xchg   %ax,%ax               ; } NOP padding to reach
  nopl   (%rax)                 ; } the next 64-byte boundary
  nopl   0x0(%rax,%rax,1)       ; }
  nopw   0x0(%rax,%rax,1)       ; }
                                ;
G1BS::store:                    ; <- now at 0x16c0 (64-byte aligned)
  push   %rbx
  mov    %esi,%ebx
  ...
```

These NOPs are dead code. Execution flows from the previous function's `ret` back to whoever called it; it never falls through the NOP sled. The padding exists purely to position the next function's entry point at a favorable address.

### The Binary Size Cost

NOP padding increases code section size. Each function wastes up to 63 bytes (on average, 31 bytes) of NOP padding. For a microbenchmark with a handful of functions, this is negligible. For a production binary with thousands of functions, the cumulative cost matters: more I-cache footprint, more TLB pressure, more pages to load.

For production code, don't use `-falign-functions=64` globally. Instead, apply `__attribute__((aligned(64)))` selectively to the specific hot functions you've measured:

```cpp
__attribute__((aligned(64)))
void G1BS::store(void* addr, int value) {
    // hot path
}
```

For microbenchmarks, use `-falign-functions=64 -falign-loops=64` globally. The binary size cost is irrelevant, and you need every function aligned to get clean measurements.

## What This Means for Your Benchmarks

In a measurement range of 1-3 ns per call, a 0.48-0.96 ns alignment artifact is 16-50% of the measured signal. That's not noise you can average away. It's a systematic error that biases every run of a particular binary in the same direction. Rebuild with a different compiler version, link order, or even an extra `#include`, and the function lands at a different address. Your "regression" might just be a cache line boundary that moved.

If you publish or consume C++ microbenchmarks, report the alignment flags. Every benchmark methodology section should list them alongside the optimization level, target architecture, and iteration count. If you don't see `-falign-functions` and `-falign-loops` in someone else's methodology section, treat sub-nanosecond differences between compiler versions as unverified, because they could be real codegen wins or pure layout artifacts and there's no way to tell from the numbers alone.

When you see a performance swing between compiler versions, check alignment before blaming the optimizer. Rebuild with `-falign-functions=64` and see if the difference persists. If it vanishes, the "regression" was a layout accident. If it persists, the compiler actually changed something meaningful.

The difference is that alignment artifacts disappear when you control the variable, and real improvements don't. Thirty-six runs across a twelve-cell matrix separated the two in about an hour of wall time. That's a small price for knowing which numbers you can trust.

This post controlled for alignment in a dispatch benchmark because that's what this series is about. But the effect is general. Any microbenchmark that measures a function call in the low-nanosecond range is susceptible. Sorting algorithms, hash table probes, serialization routines, parser inner loops. If you're measuring a tight function and comparing across compiler versions or build configurations, alignment is a confound until you prove otherwise.

Next time: a standalone deep-dive that isolates the alignment effect with a minimal, single-function benchmark, walks through the full DSB and uop-cache mechanics on Skylake, and provides a cross-compiler flag reference for GCC, Clang, and MSVC.

---

*Benchmarks run on Intel Xeon Gold 6130 @ 2.10 GHz, single core via `taskset -c 0`. GCC 11.4.0, 13.4.0, 15.2.0 (conda-forge). GCC 15 required `-static` due to glibc version mismatch on the benchmark host. Baseline: `-O2 -march=skylake-avx512 -fcf-protection`; alignment flags varied as the experimental variable. Function offsets within each binary are deterministic; what changes across compiler versions and alignment flags is the relative placement chosen by the linker, not run-to-run randomization from ASLR. 100M iterations, 1M warmup, best of 3 runs. `perf stat` on Linux 5.15, perf 5.15. Benchmark source: [cpp-dispatch-benchmark](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark).*

*Series start: [Four Ways to Dispatch a Runtime-Selected Strategy in C++]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %})*

*Previously: [When Dispatch Mechanism Choice Stops Mattering]({% post_url 2026-06-02-when-dispatch-mechanism-choice-stops-mattering %})*
