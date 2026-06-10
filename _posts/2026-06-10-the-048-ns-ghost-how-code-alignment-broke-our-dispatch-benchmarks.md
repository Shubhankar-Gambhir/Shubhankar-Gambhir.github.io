---
title: "The 0.48 ns Ghost: How Code Alignment Broke Our Dispatch Benchmarks"
date: 2026-06-10
categories: [C++, Performance]
tags: [dispatch, alignment, benchmarks, perf-stat, cache-line, dsb, microbenchmark]
mermaid: true
description: >-
  Virtual dispatch measured 2.87 ns on GCC 11 and 2.39 ns on GCC 13. Same source,
  same flags, same hardware. The investigation that traced a phantom 20% swing
  to code alignment artifacts, and what it means for every C++ microbenchmark you've
  trusted.
---

In [Part 4]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %}), I reported that virtual dispatch measured 2.87 ns on GCC 11 and 2.39 ns on GCC 13 without alignment flags, and promised a separate investigation. Same source code, same optimization flags, same hardware. A 20% performance swing between compiler versions with no visible codegen explanation.

This is that investigation.

## Reproducing the Ghost

The first step was to run a systematic matrix: four dispatch mechanisms, three GCC versions, three alignment settings. Twelve combinations, each measured best-of-3 on the same Xeon Gold 6130 core.

| Mechanism | GCC | Default | align=32 | align=64 |
|-----------|-----|---------|----------|----------|
| virtual | 11 | 2.87 | 2.42 | 2.40 |
| virtual | 13 | 2.39 | 2.39 | 2.39 |
| virtual | 15 | 2.87 | 2.39 | 2.39 |
| fnptr | 11 | 2.39 | 2.39 | 2.39 |
| fnptr | 13 | 3.35 | 2.39 | 2.39 |
| fnptr | 15 | 3.35 | 2.39 | 2.39 |
| variant | 11 | 3.62 | 3.63 | 3.59 |
| variant | 13 | 1.44 | 1.44 | 1.44 |
| variant | 15 | 1.44 | 1.44 | 1.44 |
| crtp | 11 | 2.87 | 2.39 | 2.39 |
| crtp | 13 | 3.35 | 2.39 | 2.39 |
| crtp | 15 | 2.39 | 2.39 | 2.39 |

Stare at the default column for a moment.

Function pointer on GCC 13: 3.35 ns. Add `-falign-functions=64 -falign-loops=64` and it drops to 2.39 ns. That's a 0.96 ns swing, 40% of the measured signal, in a benchmark where the source code and compiler optimizations are identical between the two builds. The only thing that changed is where the linker placed the function in the binary.

CRTP on GCC 13 shows the same pattern: 3.35 ns default, 2.39 ns aligned. Same 0.96 ns gap. Virtual dispatch on GCC 11 and GCC 15 shows the subtler version: 2.87 ns default, 2.40 ns aligned. A 0.47 ns improvement from adding two compiler flags that don't affect the generated instructions at all.

Now look at the aligned columns. With `-falign-functions=64 -falign-loops=64`, every non-variant mechanism converges to 2.39-2.40 ns regardless of GCC version. The 20% difference between GCC 11 and GCC 13 that prompted this investigation evaporates. It was never a compiler improvement. It was the linker happening to place the function at a favorable address in one build and an unfavorable one in another.

The variant row tells a different story. Its numbers are 3.62 ns on GCC 11 and 1.44 ns on GCC 13 under all three alignment settings. The 60% improvement is real: it comes from the switch-based `std::visit` optimization introduced in GCC 12, which I covered in [Part 4]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %}). The codegen is fundamentally different, and adding alignment flags doesn't change anything because the improvement isn't a layout artifact.

### Where the Functions Actually Land

To confirm the mechanism, I dumped the function addresses from the default and aligned binaries. Here's GCC 13:

| Binary | `G1BS::store` | `SerialBS::store` | `EpsilonBS::store` |
|--------|---------------|-------------------|--------------------|
| Default | 0x1660 (offset 32) | 0x1670 (offset 48) | 0x1690 (offset 16) |
| align=64 | 0x16c0 (offset 0) | 0x1700 (offset 0) | 0x1740 (offset 0) |

The "offset" is the position within the 64-byte cache line. With default alignment, `G1BS::store` starts at byte 32 of its cache line, right at the midpoint. With `-falign-functions=64`, every function starts at byte 0. The compiler inserted NOP padding before each function to push the entry point to a 64-byte boundary. Those NOPs are never executed; they just take up dead space so the function starts at a clean address.

This is the smoking gun before we even touch perf counters.

## Finding the Culprit

So I ran `perf stat` with Intel PMU events on three specimens: gcc13 with default alignment (the ghost), gcc13 with align64 (the fix), and gcc11 with default alignment (for comparison).

| Specimen | DSB uops | MITE uops | DSB miss | L1i miss | ITLB walk | ns/call |
|----------|----------|-----------|----------|----------|-----------|---------|
| virtual gcc13 default | 1,823M | 2.6M | 155K | 138K | 806 | 2.41 |
| virtual gcc13 align64 | 1,826M | 2.6M | 157K | 120K | 630 | 2.41 |
| virtual gcc11 default | 1,824M | 2.6M | 175K | 132K | 586 | 2.89 |

The data is anticlimactic, at least for virtual dispatch. The DSB (decoded stream buffer, Intel's micro-op cache) delivers roughly the same number of uops in all three cases. The MITE (the legacy decoder) handles 2.6M uops across the board. DSB misses are in the noise. The counters do not show a dramatic front-end delivery bottleneck.

The L1i miss count is the most interesting column. The aligned build has 120K instruction cache misses versus 138K for the default build, about a 13% reduction. That correlates directionally with the timing improvement, but 18K fewer L1i misses across 100M iterations is modest.

For virtual dispatch specifically, the function body is large enough that it spans multiple DSB windows even when aligned. The front-end can handle it either way. The alignment effect is there, but it's subtle.

The function pointer and CRTP cases tell the real story. Those functions are tiny: just a few instructions. When a tiny function starts in the middle of a cache line, the penalty is proportionally larger because the function has less instruction footprint to amortize the misalignment over. That's why fnptr and CRTP show 0.96 ns swings while virtual shows 0.47 ns. The mechanism is the same; the magnitude depends on function size.

The perf counters don't give us a dramatic graph to point at.

## What's Actually Happening

### Cache Line Straddling

x86 processors fetch instructions in 64-byte cache lines. When a function entry point sits near the end of a cache line, the processor needs two fetches to fill the first window of instructions: one for the tail end of the current line, one for the beginning of the next. At 100M iterations, even a single extra L1i fetch at the function entry adds up. The address table above shows `G1BS::store` at offset 32 in the default build: it's right at the midpoint, so roughly half the function's prologue lives in the next cache line.

### DSB Window Misalignment

Intel's decoded stream buffer caches decoded micro-ops in 32-byte aligned windows. Each window holds up to 6 uops from a contiguous 32-byte region of instruction bytes. A function starting at, say, offset 0x18 within a 32-byte window has only 8 bytes of usable window space before the boundary. The DSB caches the decoded uops for those 8 bytes in one window entry and the rest in the next. If the DSB can't serve a window (because it's fragmented or evicted), the processor falls back to the MITE legacy decoder, which delivers roughly 4 uops per cycle instead of up to 6.

For the function pointer benchmark, `store()` is small enough that the entire hot path fits in one or two DSB windows when aligned, but straddles three when misaligned. The function is so short that the misalignment penalty has almost no instruction footprint to amortize over. That's why fnptr and CRTP show a 0.96 ns swing while virtual (with its larger function body) shows only 0.47 ns.

### The Two-Effect Confound

The virtual dispatch ghost between GCC 11 (2.87 ns) and GCC 13 (2.39 ns) is particularly tricky because it's two effects stacked on top of each other.

GCC 13 hoists the vtable pointer into register `r12` before entering the loop. GCC 11 reloads the object pointer from the stack (`mov 0x8(%rsp),%rdi`) on every iteration. That's a real codegen improvement: one fewer memory access per call.

But the alignment data shows that the codegen improvement accounts for almost nothing. With `-falign-functions=64`, GCC 11 measures 2.40 ns and GCC 13 measures 2.39 ns. The difference is 0.01 ns, which is within measurement noise. The remaining 0.47 ns gap (2.87 minus 2.40) is pure alignment artifact.

Without controlling alignment, the codegen improvement and the alignment artifact look like one big 0.48 ns win. You'd conclude "GCC 13 generates better virtual dispatch code" and be wrong, or at least only 2% right. The alignment fix contributes 98% of the measured improvement. You need the controlled experiment to separate the two.

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

Why 64 instead of 32? Because 32-byte alignment fixes DSB window issues but doesn't guarantee cache line alignment. The data confirms this: `-falign-functions=32` brought function pointer GCC 13 from 3.35 ns all the way down to 2.39 ns (matching align=64), but for virtual GCC 11, align=32 gave 2.42 ns while align=64 gave 2.40 ns. The 0.02 ns difference is small but consistent across runs, suggesting the cache line straddling fix provides a marginal improvement on top of DSB alignment for virtual dispatch's larger function body.

### What the Padding Looks Like

With `-falign-functions=64`, the compiler inserts a NOP sled before each function entry:

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

If you publish or consume C++ microbenchmarks, report the alignment flags. Every benchmark methodology section should list them alongside the optimization level, target architecture, and iteration count. If you don't see `-falign-functions` and `-falign-loops` in someone else's methodology section, treat sub-nanosecond differences between compiler versions as unverified. They might be real. They might be ghosts.

When you see a performance swing between compiler versions, check alignment before blaming the optimizer. Rebuild with `-falign-functions=64` and see if the difference persists. If it vanishes, the "regression" was a layout accident. If it persists, the compiler actually changed something meaningful.

The difference is that alignment artifacts disappear when you control the variable, and real improvements don't. Thirty-six runs across a twelve-cell matrix separated the two in about an hour of wall time. That's a small price for knowing which numbers you can trust.

This post controlled for alignment in a dispatch benchmark because that's what this series is about. But the effect is general. Any microbenchmark that measures a function call in the low-nanosecond range is susceptible. Sorting algorithms, hash table probes, serialization routines, parser inner loops. If you're measuring a tight function and comparing across compiler versions or build configurations, alignment is a confound until you prove otherwise.

Next time: a standalone deep-dive that isolates the alignment effect with a minimal, single-function benchmark, walks through the full DSB and uop-cache mechanics on Skylake, and provides a cross-compiler flag reference for GCC, Clang, and MSVC.

---

*Benchmarks run on Intel Xeon Gold 6130 @ 2.10 GHz, single core via `taskset -c 0`. GCC 11.4.0, 13.4.0, 15.2.0 (conda-forge). GCC 15 required `-static` due to glibc version mismatch on the benchmark host. Baseline: `-O2 -march=skylake-avx512 -fcf-protection`; alignment flags varied as the experimental variable. 100M iterations, 1M warmup, best of 3 runs. `perf stat` on Linux 5.15, perf 5.15. Benchmark source: [cpp-dispatch-benchmark](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark).*

*Previously: [When Dispatch Mechanism Choice Stops Mattering]({% post_url 2026-06-02-when-dispatch-mechanism-choice-stops-mattering %})*
