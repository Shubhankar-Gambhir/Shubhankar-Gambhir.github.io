---
title: "The Alignment Cliff: Tracing a 0.478 ns Penalty to Skylake's Micro-Op Cache"
date: 2026-06-16
categories: [C++, Performance]
tags: [alignment, skylake, dsb, micro-op-cache, cache-line, perf-stat, front-end, uops-not-delivered]
description: >-
  Part 6 found a step function: 2.392 ns below some offset threshold, 2.870 ns above it.
  This post builds a controlled single-function experiment to find the exact threshold,
  instruments it with Skylake PMU counters, and traces the 0.478 ns cliff to a 38-byte
  function body crossing a 32-byte DSB window boundary.
image:
  path: /assets/img/og/the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.png
  alt: "The Alignment Cliff: Tracing a 0.478 ns Penalty to Skylake's Micro-Op Cache"
  hero: false
---

[Part 6]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}) ended with an observation and a promise. After controlling alignment artifacts in the dispatch benchmarks, I ran a sanity sweep: placed a single function at every offset within a 64-byte cache line and measured call latency. The result was a perfect step function: 2.392 ns from offset 0 through offset 24, then 2.870 ns from offset 32 through offset 56. The threshold was somewhere between 24 and 28.

This post is the full investigation. You will see the exact threshold (between byte 24 and byte 28), the Skylake PMU counters that identify the cause, and the geometric reason the cliff lands exactly where it does.

## The Experiment

The setup is a two-translation-unit design. The function under measurement, `work()`, lives in its own `.c` file:

```c
long work(long x) {
    long y = x * 2654435761L;
    y ^= (unsigned long)y >> 13;
    y *= 1099511628211L;
    return y;
}
```

The harness calls it through a volatile function pointer:

```cpp
extern "C" long work(long x);
static long (*volatile pwork)(long) = work;

// in main():
for (long i = 0; i < ITERS; ++i) acc += pwork(i);
```

The volatile pointer is load-bearing. A direct call gives the decoder a statically known target; the processor prefetches it regardless of alignment, and the cliff disappears. The volatile pointer forces a real indirect call on every iteration, matching the dispatch benchmarks from [Parts 1-5]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}) where callee alignment was what mattered.

### Placing the Function at a Controlled Offset

Compilers have their own alignment policies. GCC 15.2.0 with `-march=skylake-avx512` defaults to 16-byte function alignment; Clang 22.1.4 similar. To put `work()` at an arbitrary byte offset within a 64-byte cache line, the script compiles it to assembly with self-alignment disabled (`-falign-functions=1`), then injects a shim:

```bash
$CC -O2 -march=skylake-avx512 -fcf-protection -falign-functions=1 \
    -S benchmarks/sweep_work.c -o work.s

awk -v n="$N" '
    /^work:/ && !done {
        print ".p2align 6"            # align section to 64-byte boundary
        if (n > 0) printf ".skip %d,0x90\n", n   # N NOPs before work:
        done=1
    }
    { print }' work.s > work.shim.s
```

`.p2align 6` ensures the preceding boundary is 64-byte aligned. The `.skip N,0x90` injects N bytes of `0x90` (NOP) padding before the `work:` label, pushing the entry point to byte N within the cache line. Every binary is verified with `nm` after linking: the achieved offset is `entry_address % 64`, and any mismatch aborts the run.

## The Sweep

### 8-Step Coarse Pass

The first sweep samples every 8 bytes across one 64-byte cache line (GCC 15.2.0, best of 3 runs, `taskset -c 0`):

| Offset | ns/call |
|--------|---------|
| 0      | 2.3924  |
| 8      | 2.3922  |
| 16     | 2.3922  |
| 24     | 2.3921  |
| **32** | **2.8703** |
| 40     | 2.8707  |
| 48     | 2.8705  |
| 56     | 2.8700  |

The cliff falls somewhere between offset 24 and offset 32. Everything on the left side clusters within 0.0003 ns of each other; everything on the right side within 0.0007 ns. This is not noise. It is a genuine binary switch.

### 4-Step Fine Pass

A second sweep at 4-byte intervals narrows the threshold:

| Offset | ns/call |
|--------|---------|
| 0      | 2.3948  |
| 4      | 2.3924  |
| 8      | 2.3926  |
| 12     | 2.3920  |
| 16     | 2.3920  |
| 20     | 2.3919  |
| **24** | **2.3920** |
| **28** | **2.8701** |
| 32     | 2.8702  |
| 36     | 2.8702  |
| 40     | 2.8701  |
| 44     | 2.8723  |
| 48     | 2.8712  |
| 52     | 2.8704  |
| 56     | 2.8705  |
| 60     | 2.8702  |

Offset 24 is still on the fast side. Offset 28 is already on the slow side. The cliff is between them.

```
# bars scaled to the ns range: | = 2.39 ns (min), full bar = 2.87 ns (max)
gcc15_fine
  off  0   2.39 ns |
  off  4   2.39 ns |
  off  8   2.39 ns |
  off 12   2.39 ns |
  off 16   2.39 ns |
  off 20   2.39 ns |
  off 24   2.39 ns |
  off 28   2.87 ns | ############################################
  off 32   2.87 ns | ############################################
  off 36   2.87 ns | ############################################
  off 40   2.87 ns | ############################################
  off 44   2.87 ns | ############################################
  off 48   2.87 ns | ############################################
  off 52   2.87 ns | ############################################
  off 56   2.87 ns | ############################################
  off 60   2.87 ns | ############################################
```

No gradual degradation as the offset increases. The function runs at exactly one of two speeds, which already constrains the mechanism: whatever is happening, it is a threshold effect, not a proportional one.

## The PMU Investigation

To identify the mechanism, I ran `perf stat` with four Skylake PMU events on two pre-built binaries: the fast case (offset 0) and the slow case (offset 32). The event set was chosen to distinguish between three hypotheses: DSB-to-MITE fallback, instruction cache miss, and instruction delivery rate reduction.

```bash
taskset -c 0 perf stat \
    -e idq.dsb_uops,dsb2mite_switches.penalty_cycles,\
       idq_uops_not_delivered.core,icache_16b.ifdata_stall,\
       instructions,cycles \
    ./build/bench_gcc15_${OFFSET}
```

Results (100M iterations, GCC 15.2.0, perf 4.18.0, Linux 4.18):

| Counter | Offset 0 (fast) | Offset 32 (slow) |
|---------|-----------------|------------------|
| `idq.dsb_uops` | 1,617,247,547 | 1,616,363,959 |
| `dsb2mite_switches.penalty_cycles` | 31,207 | 23,802 |
| `idq_uops_not_delivered.core` | 406,246,006 | 810,205,780 |
| `icache_16b.ifdata_stall` | 90,061 | 93,398 |
| instructions | 1,617,707,831 | 1,617,461,585 |
| cycles | 507,408,912 | 607,783,707 |
| **IPC** | **3.19** | **2.66** |
| **ns/call** | **2.392** | **2.870** |

The table rules out two hypotheses immediately.

**DSB-to-MITE fallback is not the cause.** `idq.dsb_uops` is flat: 1.617 billion uops from the DSB in the fast case, 1.616 billion in the slow case. The micro-op cache is delivering essentially all the uops in both runs. `dsb2mite_switches.penalty_cycles` is 31K versus 24K, effectively zero over 100 million iterations. The micro-op cache never falls back to the legacy decoder in a meaningful way.

**Instruction cache misses are not the cause.** `icache_16b.ifdata_stall` is 90K versus 93K. A 3K difference across 100M iterations is noise. Both binaries fit comfortably in L1i; the cache lines are warm throughout the run.

**The signal is `idq_uops_not_delivered.core`.** This event counts uop delivery slots that the front-end left empty when the back-end was ready to accept work. In the fast case: 406 million missed slots over 507 million cycles, about 0.80 per cycle. In the slow case: 810 million missed slots over 608 million cycles, about 1.33 per cycle. The front-end is delivering uops to the IDQ (Instruction Decode Queue) at a lower rate in the slow case: not zero, not occasionally, but consistently across every one of 100 million iterations.

The IPC numbers confirm the bottleneck is entirely in the front-end. At offset 0, the back-end executes 3.19 uops per cycle. At offset 32, 2.66. The 0.53 uop/cycle shortfall matches the increase in uops_not_delivered (1.33 - 0.80 = 0.53) almost exactly. The back-end executes every uop the front-end delivers, with nothing left idle. The slowdown is one extra cycle per call where the front-end cannot fill the IDQ.

## The Geometry

The assembly that GCC 15.2.0 emits for `work()` at `-O2 -march=skylake-avx512 -fcf-protection`:

```nasm
work:
    endbr64                         ; 4 bytes  (offset  0)
    mov    $0x9e3779b1,%eax         ; 5 bytes  (offset  4)
    imul   %rax,%rdi                ; 4 bytes  (offset  9)
    mov    %rdi,%rax                ; 3 bytes  (offset 13)
    shr    $0xd,%rax                ; 4 bytes  (offset 16)
    xor    %rax,%rdi                ; 3 bytes  (offset 20)
    movabs $0x100000001b3,%rax      ; 10 bytes (offset 23)
    imul   %rdi,%rax                ; 4 bytes  (offset 33)
    retq                            ; 1 byte   (offset 37)
                                    ; total:     38 bytes
```

Verify on [Compiler Explorer](https://godbolt.org) by pasting `benchmarks/sweep_work.c` with GCC 15 at `-O2 -march=skylake-avx512 -fcf-protection`.

Clang 22.1.4 emits a near-identical 38-byte body (using `%rcx` as the intermediate register rather than `%rdi`). The function body is 38 bytes in both compilers.

Skylake's micro-op cache (DSB) organizes decoded uops into 32-byte aligned windows, each holding up to 6 uops from a contiguous 32-byte instruction region (Intel Optimization Reference Manual, §3.4.2.5). On Skylake, the processor typically delivers uops from a single DSB window per cycle. When a function call is the tight inner loop, the cycle budget per call is dominated by how many DSB window transitions the processor must make.

For `work()` at entry offset 0 (within a 64-byte cache line, whose start is 64-byte aligned at address A):

- **DSB window 0** (A+0 to A+31): contains work() bytes 0-31: `endbr64` through the first 9 bytes of `movabs`
- **DSB window 1** (A+32 to A+63): contains work() bytes 32-37: the last byte of `movabs`, `imul`, and `retq`

Two DSB windows. One transition per call.

For `work()` at entry offset 28:

- **DSB window 0** (A+0 to A+31): contains the NOP shim (bytes 0-27) and the first 4 bytes of work() (bytes 28-31)
- **DSB window 1** (A+32 to A+63): contains work() bytes 4-35 (32 bytes)
- **DSB window 2** (A+64 to A+95): contains work() bytes 36-37: the last byte of `imul` and `retq`

Three DSB windows. Two transitions per call. One extra window switch that was not there at offset 0.

The transition from window 1 to window 2 is also a cache line boundary transition: window 1 ends at A+63, window 2 starts at A+64. The processor must issue an L1i fetch request for the second cache line to complete the function. Even with both lines warm in L1i, the pipeline appears unable to overlap the two fetch requests. The extra cache line fetch serializes with the first, and that extra cycle is what `uops_not_delivered.core` records.

The threshold at offset 27 falls directly out of the arithmetic: a 38-byte function starting at offset N straddles a 64-byte cache line boundary when N + 37 >= 64, i.e., when N >= 27. Our fine sweep finds the cliff between offsets 24 and 28, consistent with 27 being the exact boundary (which falls between our two sample points).

The same geometry explains why the cliff is also a DSB window boundary: at offset 26, bytes 26-31 fill window 0 and bytes 32-63 fill window 1, with the function ending at byte 63 (the last byte of both window 1 and the cache line). At offset 27, the `retq` lands at byte 64, the start of window 2. Cache line boundary and DSB window boundary coincide because 64 and 32 share the factor 32, and 64 - 38 = 26 is the exact capacity of two DSB windows for this function.

These parameters are Skylake-specific. AMD Zen 2+ organizes its uop cache in 64-byte aligned chunks with a different capacity model, so the cliff point will differ. The general mechanism (DSB window boundary crossing raising `uops_not_delivered`) is shared across modern x86 designs, but you would need to re-run the sweep on each microarchitecture to find its specific threshold.

## Cross-Compiler Validation

If this is a hardware effect, not a codegen artifact, GCC and Clang should show identical cliffs. Clang 22.1.4 on the same Xeon Gold 6130:

| Offset | GCC 15.2.0 | Clang 22.1.4 |
|--------|------------|--------------|
| 0      | 2.3924     | 2.3919       |
| 8      | 2.3922     | 2.3919       |
| 16     | 2.3922     | 2.3920       |
| 24     | 2.3921     | 2.3919       |
| **32** | **2.8703** | **2.8703**   |
| 40     | 2.8707     | 2.8701       |
| 48     | 2.8705     | 2.8701       |
| 56     | 2.8700     | 2.8706       |

```
gcc15
  off  0   2.39 ns |
  off  8   2.39 ns |
  off 16   2.39 ns |
  off 24   2.39 ns |
  off 32   2.87 ns | ############################################
  off 40   2.87 ns | ############################################
  off 48   2.87 ns | ############################################
  off 56   2.87 ns | ############################################

clang
  off  0   2.39 ns |
  off  8   2.39 ns |
  off 16   2.39 ns |
  off 24   2.39 ns |
  off 32   2.87 ns | ############################################
  off 40   2.87 ns | ############################################
  off 48   2.87 ns | ############################################
  off 56   2.87 ns | ############################################
```

2.3919 ns vs 2.3924 ns. 2.8703 ns vs 2.8703 ns. The cliff is 0.478 ns on both compilers, with zero meaningful difference in either regime. This is a hardware effect. The compiler version has nothing to say about it.

## Controlling the Effect

### GCC

```bash
# All functions in the translation unit
-falign-functions=64

# Per-function, from source
__attribute__((aligned(64)))
void hot_function(...) { ... }
```

### Clang / LLVM

```bash
# GCC-compatible flag (accepted by Clang)
-falign-functions=64

# LLVM back-end knob (N is a log2 exponent: 6 means 2^6 = 64)
-mllvm -align-all-functions=6
```

Both flags were verified empirically on the benchmark host: with either flag, the second function in the binary lands at a 64-byte boundary, and the cliff disappears.

### MSVC

There is no command-line equivalent to `-falign-functions=N` for per-function code alignment on MSVC. `__declspec(align(N))` aligns data, not function code. The available levers are coarser:

- `/Gy` enables function-level linking (COMDAT), giving the linker more freedom to place functions, but no alignment guarantee.
- Linker `/ALIGN:n` sets section alignment, not per-function alignment.
- `/FUNCTIONPADMIN[:n]` inserts padding before functions for hot-patching, not for cache-line alignment.

Readers on MSVC who observe alignment-induced regressions should verify on their toolchain via Compiler Explorer. The `__attribute__((aligned(64)))` decoration is accepted by MSVC as a non-standard extension in some versions, but behavior is not guaranteed.

### Benchmark Methodology Checklist

If you are writing a microbenchmark that measures a call in the 1-3 ns range:

- Always include `-falign-functions=64 -falign-loops=64` in the compile flags.
- Verify the achieved alignment with `nm binary | awk '$3 == "function_name" { print $1 }'` and compute `address % 64`.
- Measure across at least a few offsets to confirm the fast path is stable, not accidentally on the favorable side of a cliff.
- Report alignment flags in the methodology section alongside optimization level and target.

A benchmark that omits alignment flags is measuring function placement as much as it is measuring the code. Placement changes when you change the compiler version, link order, or even add an unrelated `#include` that shifts everything downstream.

## What This Means in Practice

The 0.478 ns penalty applies when the tight inner loop calls a small callee through an indirect pointer. Virtual dispatch, function pointers, and `std::function` are all in scope. A callee that is large relative to the cache line size is less affected because the penalty is amortized over more work per call.

In dispatch benchmarks, where the function body is intentionally minimal to isolate the dispatch cost, the penalty is proportionally large. That is why the [alignment matrix in Part 6]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}) showed swings of 0.47-0.96 ns across GCC versions for small dispatch shims, while larger functions showed smaller swings. The smaller the function, the higher the fraction of call overhead that the alignment penalty represents.

For production hot paths, measuring and fixing alignment should follow profiling, not precede it. If `perf stat` shows `idq_uops_not_delivered.core` is high and `idq.dsb_uops` is flat, alignment is a candidate; check function addresses with `nm` and add `__attribute__((aligned(64)))` to the specific hot callee. Applying `-falign-functions=64` globally wastes up to 63 bytes per function in NOP padding, which increases I-cache footprint and can hurt more than it helps in code-size-sensitive paths.

The benchmark source, sweep scripts, and result CSVs are in the [companion repo](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark).

{% include newsletter.html %}

---

*Benchmarks run on Intel Xeon Gold 6130 @ 2.10 GHz, single core via `taskset -c 0`. GCC 15.2.0 and Clang 22.1.4 (conda-forge, micromamba). GCC 15 linked with `-static` due to glibc version mismatch on the benchmark host (host: glibc 2.28; conda env: glibc 2.34). Compile flags: `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=1` for the work object (alignment disabled); harness compiled with `-falign-functions=64 -falign-loops=64`. Offsets achieved and verified with `nm` before each measurement. 100M iterations, 1M warmup, best of 3 runs. `perf stat` on Linux 4.18.0, perf 4.18. Perf events: `idq.dsb_uops`, `dsb2mite_switches.penalty_cycles`, `idq_uops_not_delivered.core`, `icache_16b.ifdata_stall`. 4 GP events + 2 fixed counters (instructions, cycles): no multiplexing on Skylake.*

*Series: [Four Ways to Dispatch a Runtime-Selected Strategy in C++]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %})*

*Previously: [The 0.48 ns Ghost: How Code Alignment Broke Our Dispatch Benchmarks]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %})*
