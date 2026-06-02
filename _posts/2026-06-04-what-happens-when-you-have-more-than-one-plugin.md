---
title: "What Happens When You Have More Than One Plugin?"
date: 2026-06-04
categories: [C++, Performance]
tags: [dispatch, virtual, crtp, variant, function-pointer, benchmarks, branch-prediction]
description: >-
  The benchmarks in Parts 1-4 always dispatched to a single plugin type. Here's
  what happens when you mix multiple plugins in the same hot loop -- and which
  dispatch mechanism degrades most gracefully.
---

Every benchmark in this series has been a lie of omission.

We selected one plugin at startup, then hammered it 100 million times. The branch predictor learned the target on the first few iterations and predicted it perfectly for the remaining 99,999,990. That's the nicest possible scenario for indirect dispatch -- and the least realistic one.

Real systems don't work that way. A JVM's garbage collector barrier fires on every heap write, and in a generational collector, the write pattern determines *which* barrier runs. An audio pipeline routes samples through different effects depending on frequency band. A network stack selects protocol handlers based on packet headers. In every case, the hot loop sees a mix of concrete types, and the dispatch mechanism has to cope with targets that change from call to call.

This post measures what happens when you take the four mechanisms from [Part 1]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}) and feed them polymorphic workloads: round-robin, weighted random, and uniform random. The results change the decision framework.

## Setup

Same hardware as the rest of the series: Intel Xeon Gold 6130 @ 2.10 GHz. Two compilers: GCC 11.4.0 and GCC 15.2.0 (the bookends from [Part 4]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %})). Same flags: `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`.

Each benchmark pre-generates a pattern array of 1M plugin indices (Epsilon=0, Serial=1, G1=2) before timing begins. The hot loop walks this array, dispatching to whichever plugin the index selects, for 100M total iterations. No allocation, no branching on the pattern itself -- just the dispatch mechanism under test.

Three workloads, each with a different degree of branch predictor friendliness:

- **Round-robin**: Epsilon, Serial, G1, Epsilon, Serial, G1, ... A perfectly periodic pattern with period 3. The branch predictor can learn this.
- **Weighted 90/10**: 90% G1, 10% Serial, randomly distributed. Close to a realistic JVM workload where most writes hit the generational barrier but some go through a simpler path.
- **Uniform random**: Equal probability of Epsilon, Serial, or G1 on each call. Pure chaos for the branch predictor.

For reference, here are the monomorphic baselines from Parts 1 and 4 (G1 plugin, 100M calls to the same target):

| Mechanism | GCC 11 (ns/call) | GCC 15 (ns/call) |
|-----------|-------------------|-------------------|
| Direct call (baseline) | 1.48 | 1.44 |
| Virtual dispatch | 2.90 | 2.42 |
| Function pointer | 2.43 | 2.42 |
| std::variant + std::visit | 3.71 | 1.47 |
| Decoupled CRTP | 2.42 | 2.41 |

Now let's see what happens when the target changes.

## Round-Robin: The Learnable Pattern

| Mechanism | GCC 11 (ns/call) | GCC 15 (ns/call) |
|-----------|-------------------|-------------------|
| Virtual dispatch | 3.05 | 3.05 |
| Function pointer | 2.88 | 2.88 |
| std::variant + std::visit | 4.65 | 2.24 |
| Decoupled CRTP | 2.87 | 2.88 |

Round-robin is the gentlest polymorphic pattern. The target cycles through three values with perfect periodicity, and modern branch predictors handle repeating patterns well. The CPU can learn the sequence and predict each indirect branch correctly most of the time.

Two things jump out immediately.

First, **CRTP and function pointer are identical**. Not "close" -- identical. 2.87 vs 2.88 on GCC 11, 2.88 vs 2.88 on GCC 15. This is the design prediction from Part 2 playing out: after lazy resolution, decoupled CRTP collapses to a function pointer array. Under monomorphic dispatch, the two mechanisms looked similar but could have diverged due to code layout or alignment effects. Under polymorphic dispatch, they converge exactly. The function pointer *is* the dispatch mechanism in both cases; CRTP just gave you composability on top.

Second, **variant on GCC 15 is the fastest indirect mechanism at 2.24 ns**. That's faster than function pointer (2.88 ns) and faster than virtual (3.05 ns). The switch-based `std::visit` from GCC 12+ generates a `switch` on the variant index, which the compiler lowers to a jump table. For a repeating three-element pattern, the CPU's indirect branch predictor handles this jump table better than a function pointer call -- the jump target is predictable, and the switch structure gives the optimizer more to work with than an opaque indirect call.

Why does the switch outperform a function pointer here? Under monomorphic dispatch in Part 4, the compiler hoisted the switch out of the loop entirely -- one check, then a tight loop body with no dispatch at all. Under round-robin, the switch has to execute on every iteration (the variant index changes), but the jump table targets are still direct jumps within the same function. A function pointer call is an indirect `call` instruction: push return address, jump to an unknown location. A jump table hit is an indirect `jmp` within a known function. The branch predictor treats these differently, and for a short repeating pattern, the jump table wins.

On GCC 11, variant is worst in class at 4.65 ns. The old function-pointer-table `std::visit` implementation stacks two overheads: the lambda capture round-trip from [Part 3]({% post_url 2026-05-19-why-std-visit-may-be-slower-than-a-vtable %}) and the new cost of cycling through three different dispatch targets. The vtable-style implementation was already slower for one target; now it's dispatching through three.

Virtual dispatch shows a mild increase from its monomorphic baseline (2.90 to 3.05 on GCC 11), but not much. The vtable lookup is the same two dependent loads regardless of how many types pass through -- the cost comes from branch misprediction on the indirect call, and a period-3 pattern is easy enough to predict.

## Weighted 90/10: The Realistic Workload

| Mechanism | GCC 11 (ns/call) | GCC 15 (ns/call) |
|-----------|-------------------|-------------------|
| Virtual dispatch | 5.60 | 5.58 |
| Function pointer | 4.67 | 4.63 |
| std::variant + std::visit | 7.08 | 4.31 |
| Decoupled CRTP | 4.65 | 4.63 |

This is the workload that most resembles production. 90% of calls hit G1 (the heavyweight barrier with pre and post barriers), and 10% hit Serial (post-barrier only). The distribution is random, seeded deterministically -- so the branch predictor can't learn a repeating pattern, but it *can* learn the dominant target and take its chances.

The numbers are significantly higher across the board. Virtual dispatch nearly doubled from its round-robin number (3.05 to 5.58 on GCC 15). Function pointer and CRTP jumped from 2.88 to 4.63. The misprediction penalty is real: when the predictor guesses G1 (which is correct 90% of the time), the 10% Serial calls cause pipeline flushes that cost 15-20 cycles each on Skylake.

To put that in perspective: even at a 10% misprediction rate, those flushes dominate the total cost. A correctly predicted indirect call on this hardware takes about 1 ns. A mispredicted one takes 15-20 ns. So 90% of calls cost ~1 ns and 10% cost ~17 ns, giving an expected average around 2.6 ns of branch prediction cost alone -- which is roughly the gap between the monomorphic and weighted numbers.

CRTP and function pointer remain locked together: 4.65 vs 4.67 on GCC 11, 4.63 vs 4.63 on GCC 15. At this point, treating them as distinct mechanisms is misleading. They *are* the same mechanism. CRTP is the source-level abstraction; function pointer is the runtime reality.

Virtual dispatch pays a consistent premium over function pointer: about 1 ns on both compilers. The vtable indirection (two dependent loads instead of one indirect call) translates to one extra cache access per dispatch, and that cost persists regardless of misprediction rate. Under monomorphic dispatch this gap was smaller (0.47 ns on GCC 11) because perfect prediction hid some of the latency. Under polymorphic dispatch, the mispredictions amplify the structural cost.

Variant on GCC 15 (4.31 ns) beats function pointer (4.63 ns) again, though the gap has narrowed from the round-robin case. The switch-based dispatch still gives the optimizer an edge, but random arrival order makes the jump table harder to predict than the repeating pattern did.

Variant on GCC 11 (7.08 ns) is worst in class by a wide margin. It stacks the old `std::visit` overhead on top of the misprediction penalty. If you're on GCC 11 and your workload is polymorphic, variant is the wrong choice.

## Uniform Random: The Worst Case

| Mechanism | GCC 11 (ns/call) | GCC 15 (ns/call) |
|-----------|-------------------|-------------------|
| Virtual dispatch | 17.61 | 17.57 |
| Function pointer | 14.11 | 14.15 |
| std::variant + std::visit | 18.89 | 13.95 |
| Decoupled CRTP | 14.11 | 14.15 |

Every call is a coin flip among three options, equally weighted. The branch predictor has no pattern to learn, no dominant target to bet on. Every third call (on average) is a misprediction, and each misprediction flushes the pipeline.

The numbers are striking. Function pointer went from 2.88 ns (round-robin) to 14.15 ns (random) -- a 4.9x increase. Virtual went from 3.05 to 17.57 -- a 5.8x increase. These are not dispatch overhead numbers anymore. They're branch misprediction numbers. The dispatch mechanism is a rounding error compared to the cost of guessing wrong.

But the relative order still matters.

CRTP and function pointer: 14.11 and 14.11 on GCC 11, 14.15 and 14.15 on GCC 15. Perfectly identical under maximum stress. They always were the same mechanism; random dispatch just makes it undeniable.

Virtual dispatch adds 3.4 ns over function pointer (17.57 vs 14.15 on GCC 15). That's the same ~1 ns structural cost from the vtable indirection, amplified by the higher misprediction rate. When the predictor guesses wrong, the penalty includes flushing speculative work that started from the vtable lookup -- a longer pipeline to drain.

**Variant on GCC 11 is the worst number in the entire series: 18.89 ns.** The old vtable-based `std::visit` adds its own indirection layer on top of the misprediction chaos. On GCC 15, variant drops to 13.95 ns -- now matching (and slightly beating) function pointer at 14.15 ns. The switch optimization doesn't just help under monomorphic dispatch. Under maximum polymorphic stress, it transforms variant from worst-in-class to competitive-with-best.

Random dispatch is the great equalizer. When the CPU can't predict the branch, what matters is how many dependent operations sit between the misprediction and the correct target. Function pointer: one operation (load the pointer, indirect call). Virtual: two dependent operations (load vptr from the object, then load the vtable entry, then indirect call). Variant on GCC 11: two-plus (build lambda capture on the stack, index into the function pointer table, indirect call). Variant on GCC 15: one operation (the switch jump table resolves to a direct jump within the same function).

The misprediction penalty magnifies each extra operation. When the CPU flushes the pipeline after a misprediction, it has to re-execute every instruction on the correct path from the branch to the target. More instructions on that path means a longer recovery. That's why virtual (17.57 ns) is consistently slower than function pointer (14.15 ns) under random dispatch -- the extra vtable load is one more instruction the CPU must re-execute on every mispredict.

## The Full Picture

Here are all the numbers in one place, with degradation ratios from monomorphic (Parts 1/4) to uniform random:

| Mechanism | Mono GCC 11 | Mono GCC 15 | Round-robin | Weighted 90/10 | Random GCC 11 | Random GCC 15 | Degradation (GCC 15) |
|-----------|-------------|-------------|-------------|----------------|---------------|---------------|----------------------|
| Virtual | 2.90 | 2.42 | 3.05 | 5.58 | 17.61 | 17.57 | 7.3x |
| FnPtr | 2.43 | 2.42 | 2.88 | 4.63 | 14.11 | 14.15 | 5.8x |
| Variant | 3.71 | 1.47 | 4.65 / 2.24 | 7.08 / 4.31 | 18.89 | 13.95 | 9.5x |
| CRTP | 2.42 | 2.41 | 2.87 | 4.63 | 14.11 | 14.15 | 5.9x |

*Round-robin and Weighted columns show GCC 15 numbers; Variant shows GCC 11 / GCC 15 where they differ significantly.*

Five findings from the polymorphic data:

**1. Variant is the biggest GCC 15 story.** The switch optimization doesn't just help monomorphic dispatch. Under round-robin, variant drops from 4.65 to 2.24 ns (52% faster). Under random, from 18.89 to 13.95 ns. On GCC 15, variant goes from worst-in-class to best-in-class at every workload.

**2. CRTP and function pointer are provably identical.** They land at the same number in every single measurement across all three workloads and both compilers. Under polymorphic dispatch, lazy resolution collapses to a function pointer array. If you don't need composable barrier layers, a raw function pointer gives you the same runtime performance with less code.

**3. Virtual dispatch barely benefits from GCC 15.** The vtable indirection is the bottleneck, and compiler upgrades can't optimize it away. Virtual went from 2.90 to 2.42 in the monomorphic case (alignment/codegen improvements), but under random dispatch the improvement vanishes: 17.61 vs 17.57.

**4. Random dispatch is the great equalizer.** All mechanisms see 5-7x slowdown from monomorphic to random. At that point, the dispatch mechanism matters far less than the branch misprediction penalty. If your workload is truly random, spend your optimization budget on reducing the number of dispatches, not on picking a faster dispatch mechanism.

**5. Variant has the highest degradation ratio.** GCC 15 variant goes from 1.47 ns (monomorphic) to 13.95 ns (random) -- a 9.5x degradation. That's because the monomorphic number is artificially good: the compiler hoists the switch out of the loop entirely (as we showed in Part 4). Under polymorphic dispatch, the switch has to execute on every call, and variant loses its structural advantage. The monomorphic number was the outlier, not the polymorphic one.

### Updated Decision Framework

Parts 1-4 established: use virtual dispatch as the default, function pointer if you need simplicity, CRTP if you need composability, and variant if you control the compiler and need the expression problem.

Polymorphic dispatch adds one more question: **how mixed is your workload?**

If your hot loop always dispatches to the same type (monomorphic), the Part 1-4 framework holds. If your hot loop mixes types, the mechanism choice becomes less important than the branch prediction profile. The gap between the fastest and slowest mechanism shrinks from 2.5x (monomorphic, GCC 11) to 1.3x (random, GCC 15).

The practical advice: pick the mechanism that fits your design constraints (extensibility, composability, type safety). For monomorphic workloads, the performance differences are real and measurable. For polymorphic workloads, the branch predictor dominates, and all four mechanisms degrade roughly together. If you're spending your optimization budget on dispatch overhead in a polymorphic hot loop, you're optimizing the wrong thing. Batch calls by type, reduce the total number of dispatches, or restructure to avoid mixing types in the inner loop. Those changes will dwarf any gain from switching mechanisms.

---

*Benchmarks run on Intel Xeon Gold 6130 @ 2.10 GHz. GCC 11.4.0 and GCC 15.2.0 (conda-forge, statically linked). Flags: `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`. 100M iterations per measurement, 1M warmup, best of 5 runs. Pattern array: 1M entries, pre-generated before timing. PRNG seed: 42. Benchmark source: [cpp-dispatch-benchmark](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark).*

*Previously: [Your Stdlib Implementation Matters More Than the Dispatch Pattern]({% post_url 2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern %})*
