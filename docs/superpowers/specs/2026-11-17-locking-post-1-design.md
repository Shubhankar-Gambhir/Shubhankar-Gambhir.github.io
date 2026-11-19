# Locking Post 1: "Your Mutex Doesn't Know It Can Deadlock" -- Design

Date: 2026-11-17
Status: approved in chat (user changed the title; design otherwise as proposed). Amended 2026-11-19: Xeon + ARM, -O3 / -Og build matrix (user).

## Intent

The first post of the locking series, and supporting evidence for the C++Online 2027 talk "Your Mutex Doesn't Know It Can Deadlock" (talk 3 of the slate). It does not have to be live before the CFP closes (user, 2026-11-17); there is no deadline on this post. Publishing (pushing the companion repo and the blog) waits for the user's explicit OK, as with Queues Part 3.

It covers layer 1 of the talk only: a lock-rank hierarchy that catches lock-ordering inversions the first time the bad order runs.

**Thesis (corrected, verified 2026-10-14 with `~/tmp/tsan-probe.07On/abba.cpp`):** ThreadSanitizer *does* report lock-order inversions, even when the A-then-B and B-then-A paths run at different times and never deadlock, but only when both orders execute in the same process. A rank check fires on the first execution of the bad order alone, writes the ordering rule into the code, and needs no sanitizer build. In release builds it compiles to a plain `std::mutex`.

## Goals

- An idiomatic `ranked_mutex` wrapper (user's choice over a faithful HotSpot miniature), about 100 lines.
- A demo: two runs, each executing one lock order. TSan reports nothing in either run; the rank check aborts in the bad-order run with both lock names.
- Proof that release builds pay nothing: Compiler Explorer listing showing `ranked_mutex::lock()` compiles to the same code as `std::mutex::lock()` with `NDEBUG`.
- The debug-build cost of the check, measured in the queue harness.
- One sourced paragraph each positioning Linux lockdep, `absl::Mutex` deadlock detection, and Clang `-Wthread-safety` (`ACQUIRED_BEFORE`/`ACQUIRED_AFTER`).

## Non-goals

- Layers 2 and 3 of the talk (RAII wrapper family; debug-only "must not block" assertions). Later posts.
- A full head-to-head comparison with lockdep/absl/TSan. Follow-up post after the CFP.
- Same-rank acquisition and rank exceptions (legitimate out-of-order needs). Mentioned as a limitation, not built.

## Anti-goals

- Repeating the old claim that TSan misses lock-order violations.
- Implying the rank check finds deadlocks the code never exercises; it checks executed paths only, like TSan, just earlier.
- A release-build overhead claim without the codegen evidence.

## Constraints

- Blog conventions (blog `CLAUDE.md`, including its tightened "Avoiding AI Tells" rules): tell the story in the order it happened, no recurring closer heading (no "What I Learned"), at most one section-ending verdict and about two "not X, it's Y" contrasts, never invent anecdotes (leave `<!-- AUTHOR: ... -->` in the draft where a human detail belongs, and remove every such note before publishing), no em dashes, pinned compiler versions, AT&T assembly with every instruction annotated in ` ```nasm ` fences, a Compiler Explorer link per assembly snippet round-tripped through the CE API, `{% post_url %}` for internal links, methodology and footer, `/review-blog` before publishing.
- Companion code goes in `~/tmp/cpp-sync-benchmark` (GitHub `concurrent-data-structures-cpp`). Claude writes the code (user's 2026-10-22 exception to the scaffold-only rule for the locking post). No Claude attribution in commits.
- Existing queue variants stay behaviour-identical for `std::mutex`: a defaulted `Mutex` template parameter plus a small construction helper (below) so a `ranked_mutex` can receive its rank and name. (My call, from the builder check: the queues default-construct their mutexes, so a template parameter alone cannot pass ranks.)
- Hardware (user, 2026-11-19): Xeon Gold 6130 (GCC 13.4.0) and two ARM Neoverse-N1 Jenkins workers (`ostack-ub2404-16x64-arm64`, purpose "queue research", GCC 13.3.0 via Ubuntu `g++-13`). All builds of a configuration run on the same instance so they compare directly; on ARM, a gap under about 20 percent is claimed only if it shows on both instances (Part 2's rented-instance lesson). Workers are terminated when the runs finish only if the user says so; otherwise they expire at their hold.
- Build matrix (user, 2026-11-19). Common flags: `-std=c++17 -Wall -Wextra -pthread -static-libstdc++ -falign-functions=64 -falign-loops=64`, no `-march` (identical flags on both architectures). Each variant runs in its own process (Part 3's glibc lesson).

  | Build | Opt | `NDEBUG` | Mutexes measured | Purpose |
  |---|---|---|---|---|
  | release | `-O3` | defined | `std::mutex`, `ranked_mutex` | zero-cost claim (with codegen) |
  | checked | `-O3` | not defined | `ranked_mutex` | the check's own cost, isolated |
  | debug | `-Og` | not defined | `std::mutex`, `ranked_mutex` | what a developer's debug build feels |

  `-O3` rather than the queue series' `-O2` (user): the numbers stand on their own and match CMake's `Release`; the post states they are not comparable to the queue tables. Alignment flags: my call, following the blog default now that queue-series comparability no longer applies.
- HotSpot source links (`mutex.hpp`, `mutexLocker.cpp`) pinned to the jdk-27-ga tag, as in HotSpot Post 0.

## Approaches

1. **Idiomatic wrapper with a `thread_local` held-rank list (chosen).** Each `ranked_mutex` has a rank and a name; a per-thread list records held mutexes; `lock()` checks the new rank against the lowest held. Handles out-of-order unlocks and can name both locks in the report. User picked the idiomatic style; the list (over a counter) is my call.
2. **Single `thread_local` "lowest held rank" counter.** Cheaper, but wrong as soon as locks are released out of LIFO order, and cannot name the conflicting lock. Rejected.
3. **Faithful HotSpot miniature** (named rank enum, owned-lock linked list, safepoint-style exceptions). Closer to the talk's OpenJDK angle; user rejected it for this post (time).

## Decided design

### `ranked_mutex` (`locking/ranked_mutex.hpp`, new directory)

- Constructed with an integer rank and a name: `ranked_mutex tail{20, "queue tail"};`. Lower rank = acquired later (inner lock). Rule: a thread may acquire a mutex only if its rank is strictly lower than every rank it already holds (the HotSpot convention).
- `lock()`: in checked builds, scan the thread's held list; on violation, print the new and the conflicting lock's names and ranks to stderr and `std::abort()`; otherwise lock and push. `unlock()`: remove this mutex from the held list (not necessarily the top) and unlock. `try_lock()`: no rank check (it cannot block), pushes on success. Before the post compares this with HotSpot, verify against jdk-27-ga whether HotSpot's `Mutex::try_lock()` checks rank (a separate `try_lock_without_rank_check()` suggests it does); the post must not claim parity unless confirmed.
- Checked vs release: checks are on unless `NDEBUG` is defined, like `assert`. With `NDEBUG`, `ranked_mutex` holds only a `std::mutex` (rank and name compile out) and its member functions forward directly.
- Meets the Lockable requirements so `std::lock_guard`, `std::unique_lock` and `std::scoped_lock` work.

### Demo (`locking/examples/abba_demo.cpp`)

Two code paths over two mutexes, A (rank 20) and B (rank 10): path "good" locks A then B; path "bad" locks B then A. A command-line argument chooses which one path runs. Built twice: with `std::mutex` under `-fsanitize=thread` (expect: no report in either single-path run, report when both paths run in one process, reproducing the probe), and with `ranked_mutex` without `NDEBUG` (expect: the bad path aborts on first run with both names). The post shows the outputs.

### Tests (`locking/examples/test_ranked_mutex.cpp`, `make test-locking`)

Correct order passes; inverted order aborts (death test via `fork`); out-of-order unlock keeps the list consistent; `try_lock` ignores rank; works under `std::scoped_lock`/`std::unique_lock`; multithreaded stress with consistent order under TSan is clean.

### Benchmark

- Add `template <typename T, typename Mutex = std::mutex>` to `MutexQueue` and `TwoLockQueue` (two-lock tail rank above head rank, so push and pop never nest, but each lock still runs the check). Mutex members are built through a helper, e.g. `RankedSlot<Mutex> _t{20, "queue tail"};`, which forwards rank and name to `ranked_mutex` and ignores them for `std::mutex` (default-constructed, as today).
- `bench_queue` gains `--mutex {std|ranked}`. Three binaries implement the matrix above: `bench_lock_release` (`-O3 -DNDEBUG`), `bench_lock_checked` (`-O3`), `bench_lock_debug` (`-Og`).
- Mixes 1x1, 4x4, 16x16, 15x1; `MutexQueue` and `TwoLockQueue`; `int64_t` payload; 1M items per producer, 5 runs after warmup, median; one process per variant; Xeon plus both ARM instances.

### Codegen (Compiler Explorer)

GCC 13.4: a function that locks and unlocks a `ranked_mutex` vs one using `std::mutex`, at both `-O2 -DNDEBUG` and `-O3 -DNDEBUG` (identical listings expected at each level, so the zero-cost claim does not depend on the optimization level); plus the checked `-O3` build's `lock()` to show what the check costs in instructions.

### Post shape (~2,500-3,000 words)

Hook (ABBA that ships green); why tests and TSan miss the single-order case, with the probe output; the rank idea and HotSpot's version (linked); the wrapper; the demo; what it costs (checked and debug numbers, Xeon and ARM) and what it doesn't (release codegen); prior art paragraph; limitations (executed paths only, same-rank, try_lock); a closing section with its own heading, not a takeaway list, pointing at layers 2-3. Footer with repo, hardware, previous link (Queues Part 3).

## Scope boundary (my call, from the builder check)

The C++Online abstracts, including rewording talk 3's "deterministically, rather than probabilistically under load" line to match the corrected thesis, are the next task after this plan, not part of it. This plan ends with the post ready to publish.

## Left to the builder

- Exact rank values and names in examples; held-list container (small fixed array vs vector).
- Death-test mechanics; TSan flags (`detect_deadlocks` default).
- Which CE functions exactly; benchmark output folding into the existing CSV/tables tooling.
