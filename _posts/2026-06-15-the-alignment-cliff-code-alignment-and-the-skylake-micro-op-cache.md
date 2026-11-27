---
title: "The Alignment Cliff: Tracing a 0.478 ns Penalty to Skylake's Micro-Op Cache"
date: 2026-06-15
categories: [C++, Performance]
tags: [alignment, skylake, dsb, micro-op-cache, cache-line, perf-stat, front-end, uops-not-delivered]
description: >-
  Part 6 blamed code alignment for a phantom swing in the dispatch benchmarks. Placing a
  single function at every offset in a cache line turns up a step function: 2.392 ns below
  a threshold, 2.870 ns above it. Skylake PMU counters trace the 0.478 ns cliff to a 38-byte
  function body spilling across a 64-byte cache line, and rule out Part 6's explanation.
image:
  path: /assets/img/og/the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.png
  alt: "The Alignment Cliff: Tracing a 0.478 ns Penalty to Skylake's Micro-Op Cache"
  hero: false
---

[Part 6]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}) traced a 20% swing in the dispatch benchmarks to code alignment and ended by promising a minimal, single-function benchmark that isolates the effect. I started with a sanity sweep: one function placed at every 8-byte offset within a 64-byte cache line. The result was a perfect step function, 2.392 ns from offset 0 through offset 24 and 2.870 ns from offset 32 through offset 56.

Part 6 also offered a cause: misaligned code falling out of the micro-op cache onto the legacy decoder. The counters below say that's wrong, and point at something else.

## The Experiment

The function under measurement, `work()`, lives in its own `.c` file:

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

GCC 15.2.0 with `-march=skylake-avx512` defaults to 16-byte function alignment; Clang 22.1.4 similar. To put `work()` at an arbitrary byte offset within a 64-byte cache line, the script compiles it to assembly with self-alignment disabled (`-falign-functions=1`), then injects a shim:

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

The cliff falls somewhere between offset 24 and offset 32. Everything on the left side clusters within 0.0003 ns of each other; everything on the right side within 0.0007 ns.

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

![ns per call against the entry offset of work(): flat at 2.392 ns for offsets 0 through 24, flat at 2.870 ns for offsets 28 through 60](/assets/img/alignment-cliff-sweep-light.svg){: .light w="720" h="360" }
![ns per call against the entry offset of work(): flat at 2.392 ns for offsets 0 through 24, flat at 2.870 ns for offsets 28 through 60](/assets/img/alignment-cliff-sweep-dark.svg){: .dark w="720" h="360" }
_The 4-byte pass, GCC 15.2.0. Nothing was measured inside the shaded band._

The function runs at one of two speeds, which already rules out anything that scales with the offset; something crosses a boundary between byte 24 and byte 28.

## The PMU Investigation

I ran `perf stat` with four Skylake PMU events on two pre-built binaries: the fast case (offset 0) and the slow case (offset 32). I picked events that could separate three suspects: DSB-to-MITE fallback (Part 6's explanation), instruction cache misses, and a lower uop delivery rate.

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

Part 6's suspect goes first. If misaligned code were falling out of the micro-op cache, `idq.dsb_uops` would drop and `dsb2mite_switches.penalty_cycles` would climb. Neither moves: the DSB delivers 1.617 billion uops in the fast case and 1.616 billion in the slow one, and the switch penalty is 31K versus 24K cycles, effectively zero over 100 million iterations. So the fallback explanation in Part 6 was wrong. Instruction cache misses don't move either; `icache_16b.ifdata_stall` is 90K versus 93K, noise over 100M iterations, and both binaries stay warm in L1i. What does move is `idq_uops_not_delivered.core`, which counts uop delivery slots the front end left empty while the back end was ready for work. It doubles, from 406 million empty slots over 507 million cycles (about 0.80 per cycle) to 810 million over 608 million cycles (about 1.33 per cycle). The micro-op cache is still serving the uops; it just fills the IDQ (Instruction Decode Queue) less often per cycle.

The IPC numbers line up with this, but they aren't independent evidence. `instructions` and `idq.dsb_uops` are both 1.617 billion, so instructions and uops are interchangeable here, and Skylake's front end can hand over four uops per cycle. Delivered uops plus empty slots should therefore come to about four: 3.19 + 0.80 = 3.99 at offset 0, and 2.66 + 1.33 = 3.99 at offset 32. The 0.53 drop in IPC and the 0.53 rise in empty slots are the same fact counted twice. What the sum does tell you is that the back end almost never stalled, because `idq_uops_not_delivered.core` only counts slots the back end could have taken, so a back-end stall would show up as a total below four. The cost is all front end: 100 million extra cycles over 100 million calls, one cycle per call.

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

Clang 22.1.4 emits a near-identical 38-byte body (using `%rcx` as the intermediate register rather than `%rdi`).

*Update (November 2026): an earlier version of this section blamed the number of 32-byte DSB windows the function touches. The slow offsets past 32 don't fit that explanation, so this version is rewritten around the cache line instead.*

Skylake's micro-op cache (DSB) organizes decoded uops into 32-byte aligned windows, each holding up to 6 uops from a contiguous 32-byte instruction region (Intel Optimization Reference Manual, §3.4.2.5). Counting the windows each placement touches is the obvious first explanation. Lay a few placements out side by side, with A as the 64-byte aligned start of the line:

![work(), 38 bytes, placed at offsets 0, 24, 28, 32 and 60. Offsets 0 and 24 stay inside one 64-byte line and run at 2.39 ns; offsets 28, 32 and 60 cross into the next line and run at 2.87 ns. Window counts are 2, 2, 3, 2, 3.](/assets/img/alignment-cliff-placement-light.svg){: .light w="720" h="304" }
![work(), 38 bytes, placed at offsets 0, 24, 28, 32 and 60. Offsets 0 and 24 stay inside one 64-byte line and run at 2.39 ns; offsets 28, 32 and 60 cross into the next line and run at 2.87 ns. Window counts are 2, 2, 3, 2, 3.](/assets/img/alignment-cliff-placement-dark.svg){: .dark w="720" h="304" }
_Thin lines are 32-byte DSB window boundaries; heavy lines are 64-byte cache line boundaries._

A 38-byte function starting at offset N covers bytes N through N + 37. Both fast placements touch two DSB windows, but so does offset 32, which is slow, while 28 and 60 touch three. Window count doesn't sort the rows into fast and slow. The cache line does: at every fast offset, `endbr64` through `retq` sits inside one 64-byte line, and at every slow offset the function spills into the next one.

The threshold falls out of the arithmetic. The function reaches the next line when N + 37 >= 64, i.e., when N >= 27, and the fine sweep puts the cliff between 24 and 28. Every placement from 27 to 63 crosses exactly one line boundary, which is why the slow side stays flat all the way to offset 60.

The counters say where the cycle isn't, more than where it is. The uops still come from the DSB, the switch penalty to the legacy decoder stays near zero, and `icache_16b.ifdata_stall` doesn't move, so both lines are warm in L1i and nothing misses. The same uops simply take one more cycle to deliver when they come from two lines instead of one. The simplest reading is that this path can't deliver uops from two 64-byte lines in the same cycle; these four events are consistent with that, but they can't prove it.

These parameters are Skylake-specific. AMD Zen 2+ organizes its uop cache in 64-byte aligned chunks with a different capacity model, so the cliff point may differ, and each microarchitecture needs its own sweep to find its threshold.

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

Both compilers hit a 0.478 ns cliff at the same offsets.

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

There's no command-line equivalent to `-falign-functions=N` for per-function code alignment on MSVC. `__declspec(align(N))` aligns data, not function code. The available levers are coarser:

- `/Gy` enables function-level linking (COMDAT), giving the linker more freedom to place functions, but no alignment guarantee.
- Linker `/ALIGN:n` sets section alignment, not per-function alignment.
- `/FUNCTIONPADMIN[:n]` inserts padding before functions for hot-patching, not for cache-line alignment.

clang-cl accepts `__attribute__((aligned(64)))` on functions; MSVC itself does not.

### Benchmark Methodology Checklist

If you're writing a microbenchmark that measures a call in the 1-3 ns range:

- Always include `-falign-functions=64 -falign-loops=64` in the compile flags.
- Verify the achieved alignment with `nm binary | awk '$3 == "function_name" { print $1 }'` and compute `address % 64`.
- Measure across at least a few offsets to confirm the fast path is stable, not accidentally on the favorable side of a cliff.
- Report alignment flags in the methodology section alongside optimization level and target.

A benchmark that omits alignment flags is measuring function placement as much as it is measuring the code. Placement changes when you change the compiler version, link order, or even add an unrelated `#include` that shifts everything downstream.

## Where the Penalty Applies

The 0.478 ns penalty applies when the tight inner loop calls a small callee through an indirect pointer. Virtual dispatch, function pointers, and `std::function` are all in scope. A callee that is large relative to the cache line size is less affected because the penalty is amortized over more work per call. That's why the [alignment matrix in Part 6]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}) showed swings of 0.47-0.96 ns across GCC versions for small dispatch shims, while larger functions showed smaller swings.

For production hot paths, measuring and fixing alignment should follow profiling, not precede it. If `perf stat` shows `idq_uops_not_delivered.core` is high and `idq.dsb_uops` is flat, alignment is a candidate; check function addresses with `nm` and add `__attribute__((aligned(64)))` to the specific hot callee. Applying `-falign-functions=64` globally wastes up to 63 bytes per function in NOP padding, which increases I-cache footprint and can hurt more than it helps in code-size-sensitive paths.

The benchmark source, sweep scripts, and result CSVs are in the [companion repo](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark).

{% include newsletter.html %}

---

*Benchmarks run on Intel Xeon Gold 6130 @ 2.10 GHz, single core via `taskset -c 0`. GCC 15.2.0 and Clang 22.1.4 (conda-forge, micromamba). GCC 15 linked with `-static` due to glibc version mismatch on the benchmark host (host: glibc 2.28; conda env: glibc 2.34). Compile flags: `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=1` for the work object (alignment disabled); harness compiled with `-falign-functions=64 -falign-loops=64`. Offsets achieved and verified with `nm` before each measurement. 100M iterations, 1M warmup, best of 3 runs. `perf stat` on Linux 4.18.0, perf 4.18. Perf events: `idq.dsb_uops`, `dsb2mite_switches.penalty_cycles`, `idq_uops_not_delivered.core`, `icache_16b.ifdata_stall`. 4 GP events + 2 fixed counters (instructions, cycles): no multiplexing on Skylake.*

**Series start:** [Four Ways to Dispatch a Runtime-Selected Strategy in C++]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %})

**Previously:** [The 0.48 ns Ghost: How Code Alignment Broke Our Dispatch Benchmarks]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %})

**Next:** [What Does a Lock Actually Cost? Benchmarking Concurrent Counters in C++]({% post_url 2026-06-24-what-does-a-lock-actually-cost-concurrent-counter-benchmarks %}). The dispatch series ends here; the next post moves from dispatch to concurrency.
