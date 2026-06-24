# Design Spec: Synchronization Benchmark Lab + C++ Concurrency Series

- **Date:** 2026-06-24
- **Status:** Draft (awaiting user review)
- **Owner:** Shubhankar Gambhir
- **Type:** New blog series + new companion benchmark repo
- **Predecessor:** The 7-part C++ dispatch series (`_posts/2026-05-07` through `2026-06-15`)

## 1. Context and Goals

The dispatch series answered "what does a dispatch mechanism cost, and what really
drives that cost." This series asks the same kind of question about synchronization:
**what does a lock actually cost, how do real concurrent data structures behave under
contention, and how do the alternatives (JVM monitors, lock-free) compare.**

Three goals, in priority order:

1. **Learn by doing.** The author writes the benchmark code; Claude acts as guide --
   defining each experiment, its requirements, and acceptance criteria, then reviewing
   the author's implementation and giving feedback. Claude does not write the benchmarks.
2. **Learn modern C++.** Use the concurrency domain to learn C++17 and C++20/23 features
   and practices, surfaced as a measurable "baseline vs modern" axis rather than a style
   lecture.
3. **Produce a blog series.** Posts are written *from the data* the lab produces, not
   the other way around. We build and measure first; the surprises become the posts (the
   same way the alignment ghost in dispatch Parts 6-7 emerged from unexpected data).

### Working Model (non-negotiable)

Claude is the guide, the author is the builder.

- Claude specifies each experiment: what to measure, requirements, acceptance criteria.
- The author writes the benchmark / data structure / harness code.
- Claude reviews: correctness, methodology rigor, modern-C++ idiom, and statistical soundness.
- Review gates are explicit (see Section 9). Code does not advance to "measured" until it
  passes the invariant check and Claude's review.

## 2. Series Arc

Approach A from brainstorming -- cost up the stack, four posts, each one falsifiable
thesis, each standalone for HN but rewarding as a series. The arc is a guide, not a
contract: the data may reorder or split posts.

1. **The uncontended cost** -- what a lock costs when nobody competes; the implementation
   under libstdc++ (futex fast path = a CAS, not a syscall). Foundation post.
2. **When threads fight** -- contention: the futex syscall boundary, cache-line ping-pong,
   the scaling curve on the Xeon. Motivates why the two branches below exist.
3. **How the JVM dodges the syscall** -- HotSpot monitor design in OpenJDK / Zulu: thin
   (stack) locks vs inflated monitors, the object header lock word, spin-then-park. Why a
   JVM monitor often beats `std::mutex` at low contention. Public source, pinned JDK version.
4. **Lock-free, and when it's a lie** -- `std::atomic`, CAS loops, ABA, and the honest
   measurement that lock-free frequently loses to a good mutex. Myth-busting closer.

**Post structure is decided from data, not pre-committed.** We build the full lab (Section
4), run sweeps, and assign findings to posts once we see what is interesting.

## 3. The Lab: New Companion Repo

A new repo (working name `cpp-sync-benchmark`; final name TBD by author) modeled on
`cpp-dispatch-benchmark`. It inherits the dispatch repo's proven conventions:

- `Makefile` with pinned per-compiler targets via micromamba (`~/utils/mamba/envs/gccXX`)
- `benchmarks/`, `examples/`, `results/`, `scripts/` layout
- `results/` holds raw `.txt` / `.csv` plus generated markdown tables
- `scripts/` holds perf and sweep drivers
- `README.md` mirroring the dispatch repo's structure (intro, results tables, build/run)

New vs the dispatch repo: a multi-threaded harness (Section 7) and a standard-version axis
(Section 6).

## 4. Data Structures and Variant Matrix

Four concurrent data structures. The author codes **all four** before we decide post
assignment. Each has multiple implementation variants so we can compare physics.

| Structure        | Variants to implement                                              | Failure mode exposed          | JVM analogue (Post 3)        |
|------------------|-------------------------------------------------------------------|-------------------------------|------------------------------|
| Counter          | `std::mutex`; `atomic<int64_t>`; striped (LongAdder-style, padded)| contention, false sharing     | `LongAdder`                  |
| MPMC queue       | `std::mutex`+`deque`; two-lock (Michael-Scott); lock-free ring     | enqueue/dequeue contention    | `ArrayBlockingQueue`         |
| Striped hash map | single `mutex`; `shared_mutex`; N-way striped                      | lock striping, read/write split| `ConcurrentHashMap`         |
| Treiber stack    | `std::mutex`; lock-free CAS (with ABA note)                        | CAS-retry, ABA                | `ConcurrentLinkedQueue`      |

Sequencing (refined in the implementation plan): **counter first** -- smallest, every
factor shows up cleanly, and it establishes the harness and the methodology. Then queue,
hash map, stack.

## 5. Why a Separate Lab Repo

The lab lives in its own repo rather than extending `cpp-dispatch-benchmark` for two
reasons. First, naming: the dispatch repo is branded and scoped for dispatch, and each
blog post references "the companion repo" -- a synchronization series wants a
synchronization-named home. Second, divergence: the multi-threaded harness (Section 7) and
the standard-version axis (Section 6) are substantial new machinery that would mix two
unrelated series under one Makefile. A clean repo keeps each series independently
buildable and citable.

## 6. Modern C++ as a Learning Thread

A cross-cutting axis: **C++17 baseline vs C++20/23 modern variant**, per structure. Both
are built and measured. "Old way vs new way" is a results column, not just prose. The
publish decision (whether the comparison makes a post) is deferred to the data.

Standard strategy: idiomatic **C++17** baseline (portable, matches the dispatch series),
plus a **C++20/23** modern variant where the newer standard adds a relevant primitive.
GCC 11+ provides C++20, GCC 13+ provides C++23; all are in the existing mamba matrix.
GCC 15 requires `-static` on the Xeon (glibc 2.28 vs 2.34), as in the dispatch repo.

Features to learn and measure, by structure:

| Structure     | Modern C++ surfaced (learn + measure)                                                       |
|---------------|---------------------------------------------------------------------------------------------|
| Counter       | `std::atomic_ref`; `atomic::wait`/`notify`; `std::hardware_destructive_interference_size`    |
| MPMC queue    | `std::counting_semaphore`; `std::optional` returns; move/`emplace`; concepts on element type |
| Hash map      | `std::shared_mutex`; `std::scoped_lock` (deadlock-free multi-lock); transparent lookup       |
| Treiber stack | `std::atomic<std::shared_ptr>` (safe reclamation); `memory_order` on CAS; `[[nodiscard]]`    |
| Harness       | `std::jthread` + `std::stop_token`; `std::barrier`/`std::latch` vs `pthread_barrier_t`; `std::format` |

Always-on practices reviewed in every PR: RAII, rule-of-zero, `constexpr`, `[[nodiscard]]`,
concepts, structured bindings.

## 7. Multi-Threaded Harness

This is the genuinely new and hard part relative to the dispatch repo. Requirements:

- **N worker threads**, each pinned to a distinct logical core (`pthread_setaffinity_np`;
  the C++20 harness variant may use `std::jthread`).
- **Synchronized start** so we measure steady-state, not ramp-up: `pthread_barrier_t`
  baseline; `std::barrier`/`std::latch` modern variant (itself a measured comparison).
- **Throughput (ops/sec) vs thread count** -- the scaling curve -- plus per-op latency.
- **Thread-count sweep** 1, 2, 4, 8, 16, 32 (Xeon Gold 6130: 16 cores / 32 threads per
  socket; confirm socket count before NUMA sweeps).
- **Median-of-N runs** with reported spread (dispatch-series statistical rigor).
- **Post-run invariant check** -- fail loud on a race (counter == total ops, queue drained,
  stack empty, map element count correct). A concurrent benchmark with a silent race
  produces a plausible wrong number; the invariant check is the only defense.
- **Defeat elision** -- consume results into a `volatile` sink, as in the dispatch harness.

Anti-pitfall notes carried from the dispatch series: the signal lives in the sub-nanosecond
to low-nanosecond regime, where alignment and core placement dominate (Parts 6-7). Pin
cores, warm up, and run medians.

## 8. Factor Sweep Dimensions

What we vary and measure. Post 1 establishes the methodology and runs roughly 3-4 of these;
the rest cascade across Posts 2-4.

- **Implementation**: mutex vs atomic vs lock-free (the spine axis)
- **GCC version**: gcc9-15 matrix
- **Alignment / false sharing**: `alignas(64)` / `hardware_destructive_interference_size`
  padding, cache-line layout (the concurrency form of the dispatch alignment story)
- **Memory ordering**: relaxed / acq-rel / seq_cst
- **Branch prediction**: CAS-retry loop, lock fast/slow-path branch
- **Thread count / contention curve**: 1 -> 32 threads
- **Spin backoff**: naive spin vs `_mm_pause()` vs exponential backoff
- **stdlib**: libstdc++ vs libc++ (callback to dispatch Part 4)
- **Core / HT placement**: same physical core vs distinct cores (taskset)
- **NUMA**: cross-socket placement (verify box topology first)
- **C++ standard**: C++17 vs C++20/23 (Section 6)

## 9. Methodology and Review Gates

```
build structure variant  ->  invariant check passes  ->  Claude review  ->  measure (sweeps)  ->  collect results  ->  decide post assignment  ->  write post
```

Per-structure review gate (Claude reviews author's code):
1. Correctness: invariant check passes under thread sanitizer where feasible.
2. Methodology: synchronized start, warmup, median-of-N, elision defeated, cores pinned.
3. Modern-C++ idiom: RAII, rule-of-zero, appropriate standard features.
4. Statistical soundness: spread reported, enough runs, outliers explained.

Post-writing gate (per blog post, before publish):
- `/review-blog` 8-dimension audit
- Verify Jekyll build, links, rendering (`superpowers:verification-before-completion`)
- Blog writing conventions (no em dashes, pinned versions, AT&T asm, footer, methodology box)

## 10. Hardware and Toolchain

- Intel Xeon Gold 6130 @ 2.10 GHz (`xeongoldgc01.azulsystems.com`), AVX-512 capable
- GCC 9-15 via conda-forge micromamba at `~/utils/mamba/envs/gccXX`
- Benchmark flags (baseline, from dispatch series):
  `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`
  C++20/23 variants swap `-std=c++17` for `-std=c++20` / `-std=c++23`.
- GCC 15 needs `-static` on the Xeon
- JVM movement (Post 3): OpenJDK / Zulu, pinned version, public source only. No Zing internals.

## 11. Out of Scope / Deferred

- Zing (Falcon/C4) monitor internals -- proprietary, not for a public blog.
- A full memory-ordering deep dive -- teased where relevant, but the rabbit hole is its own
  potential post, not part of the lab's first pass.
- Distribution strategy (Reddit/HN batching, newsletter) -- handled per existing blog
  workflow once posts exist.

## 12. Decisions Log

- Series arc: Approach A (cost -> contention -> JVM -> lock-free), flexible. **Decided.**
- Working model: Claude guides/specifies/reviews; author codes. **Decided.**
- JVM target: OpenJDK / Zulu, public only. **Decided.**
- Vehicle: four concurrent data structures, build all before assigning posts. **Decided.**
- Repo: new companion repo (`cpp-sync-benchmark`, name TBD). **Decided.**
- Standard: C++17 baseline + C++20/23 modern variant, both built/measured. **Decided.**
- Modern C++ as an explicit, measured learning axis. **Decided.**

## 13. Success Criteria

- A new companion repo with the four data structures (all variants) plus the multi-threaded
  harness, each passing its invariant check and Claude review.
- A reproducible factor-sweep methodology (scripts + results) for the dimensions in Section 8.
- The author can explain, from their own measurements, what a lock costs uncontended and
  under contention, and why each alternative wins or loses.
- At least the first post drafted from real lab data, meeting all blog conventions.

## 14. Open Questions (resolve during hands-on)

- Final repo name.
- The Post 1 "tease" (the forward hook to a later post) -- determined once we see the data.
- Xeon socket count / NUMA topology -- verify before NUMA sweeps.
- libc++ toolchain availability on the Xeon for the stdlib-comparison factor.
