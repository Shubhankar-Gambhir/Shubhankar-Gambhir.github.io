# Locking Post 1 ("Your Mutex Doesn't Know It Can Deadlock") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a rank-checked `ranked_mutex`, prove it catches a lock-order inversion on the first bad run where TSan cannot, measure what it costs (Xeon + two ARM instances, `-O3` release/checked and `-Og` debug), and draft the post.

**Architecture:** A header-only `ranked_mutex` in a new `locking/` directory keeps a per-thread list of held locks and aborts on a rank violation unless `NDEBUG` is defined, in which case it is a bare `std::mutex`. `MutexQueue` and `TwoLockQueue` gain a defaulted `Mutex` template parameter (members built through `RankedSlot`), so the existing queue harness measures `std::mutex` against `ranked_mutex` with no change for `std::mutex`. A dedicated runner produces one CSV per (host, build).

**Tech Stack:** C++17, GCC 13.4.0 (Xeon, conda `~/utils/mamba/envs/gcc13`) / GCC 13.3.0 (ARM, Ubuntu `g++-13`), Make, bash, Python 3 stdlib, ThreadSanitizer, Compiler Explorer API, Jekyll.

**Spec:** `docs/superpowers/specs/2026-11-17-locking-post-1-design.md` (blog repo). Read it first.

## Global Constraints

- Code repo `~/tmp/cpp-sync-benchmark`, new branch `locking-post-1` off `main` (a63f4da). Namespace `sync_bench`. No `Co-Authored-By: Claude` in any commit. Nothing is pushed in this plan.
- Rank rule: a thread may acquire a mutex only if its rank is strictly lower than every rank it already holds. Equal ranks are a violation.
- Checks are on unless `NDEBUG` is defined. With `NDEBUG`, `sizeof(ranked_mutex) == sizeof(std::mutex)` and rank/name do not exist.
- Violation message, exact text, then `std::abort()`: `ranked_mutex: acquiring "<new>" (rank <n>) while holding "<held>" (rank <m>)` where `<held>` is the held lock with the lowest rank.
- Queue ranks: `MutexQueue` lock rank 10 named `"queue"`; `TwoLockQueue` tail rank 20 `"queue tail"`, head rank 10 `"queue head"`.
- Build matrix flags, common: `-std=c++17 -Wall -Wextra -pthread -static-libstdc++ -falign-functions=64 -falign-loops=64`, no `-march`. release = `-O3 -DNDEBUG`; checked = `-O3`; debug = `-Og`.
- Benchmark: `MutexQueue`, `TwoLockQueue`; mixes `1x1 4x4 16x16 15x1`; `int64_t` payload; 1M items/producer; 5 runs after warmup; median; one process per (build, mutex, variant, mix).
- Existing `std::mutex` behaviour of every queue is unchanged; `make test-queue test-queue-tsan test-queue-asan` and `bash scripts/test_run_part3.sh` stay green.
- Blog conventions (blog `CLAUDE.md`), including "Avoiding AI Tells": no em dashes, ` ```nasm ` AT&T with every instruction annotated, a CE link per snippet round-tripped through the API, `{% post_url %}`, pinned sources, no "What I Learned" closer, never invent anecdotes (`<!-- AUTHOR: ... -->` in the draft, all removed before publishing).

## Review Focus

- Locking the same `ranked_mutex` twice in one thread (self-deadlock) must abort with the violation message, not hang. Pinned in Task 1.
- Unlocking a `ranked_mutex` the thread does not hold must abort with `ranked_mutex: unlocking "<name>" which this thread does not hold`, not corrupt the list. Pinned in Task 1.
- Holding more locks than the per-thread capacity (16) must abort with `ranked_mutex: more than 16 locks held`, not overflow. Pinned in Task 1.
- `std::condition_variable_any` waiting on `std::unique_lock<ranked_mutex>` must work and leave the held list consistent after the wakeup. Pinned in Task 1.
- `std::scoped_lock` over two ranked mutexes passed in the "wrong" argument order must not abort (it locks one and `try_lock`s the rest). Pinned in Task 1.

---

### Task 1: `ranked_mutex` and its tests

**Files:**
- Create: `locking/ranked_mutex.hpp`, `locking/examples/test_ranked_mutex.cpp`, `locking/examples/test_ranked_mutex_release.cpp`
- Modify: `Makefile` (targets `test-locking`, `test-locking-tsan`), `README.md` (one `locking/` paragraph)

**Interfaces:**
- Produces, in `locking/ranked_mutex.hpp`, `namespace sync_bench`:
  - `class ranked_mutex` (non-copyable, non-movable): `ranked_mutex(int rank, const char* name)`; `void lock()`; `bool try_lock()`; `void unlock()`. Checked builds only: `int rank() const`, `const char* name() const`.
  - Checked builds keep a `thread_local` fixed array of up to 16 held `ranked_mutex*`.
  - Under `NDEBUG` the constructor still takes `(int, const char*)` and ignores them.

- [ ] **Step 1: Write the tests.** `test_ranked_mutex.cpp` (checked build), in the style of `queue/examples/test_queue.cpp` (printf `OK`/`FAIL`, `g_failures`, `ALL PASS` summary). A helper `expect_abort(name, fn, expected_substring)` forks, runs `fn` in the child with stderr captured through a pipe, and passes iff the child died of `SIGABRT` and stderr contains the substring. Cases:
  - `correct_order`: lock A(20) then B(10), unlock both. No abort.
  - `inverted_order_aborts`: lock B(10) then A(20). Expects `acquiring "A" (rank 20) while holding "B" (rank 10)`.
  - `equal_rank_aborts`: two rank-10 mutexes C and D. Expects `acquiring "D" (rank 10) while holding "C" (rank 10)`.
  - `self_relock_aborts` (Review Focus): lock A then lock A. Expects `acquiring "A" (rank 20) while holding "A" (rank 20)`.
  - `unlock_not_held_aborts` (Review Focus): unlock A without locking it. Expects `unlocking "A" which this thread does not hold`.
  - `capacity_aborts` (Review Focus): lock 16 mutexes with ranks 100, 99, ..., 85 (no abort), then a 17th with rank 84. Expects `more than 16 locks held`.
  - `out_of_order_unlock`: lock A(30), B(20); unlock A; then locking A again aborts (child, expects `acquiring "A" (rank 30) while holding "B" (rank 20)`); after unlocking B, locking A succeeds (parent).
  - `try_lock_ignores_rank`: hold B(10); `A.try_lock()` returns true with no abort; unlock both.
  - `try_lock_records_held`: after a successful `A(20).try_lock()`, locking C(25) aborts (expects `while holding "A" (rank 20)`).
  - `scoped_lock_any_order` (Review Focus): `std::scoped_lock l(b10, a20);` no abort; both unlocked after scope (locking A then B afterwards succeeds).
  - `cv_any_roundtrip` (Review Focus): thread T1 holds `std::unique_lock<ranked_mutex>` on M(10) and waits on a `std::condition_variable_any` for a flag; T2 sets it under M and notifies; after T1 wakes and releases M, T1 locks A(20) then M(10) successfully.
  - `threads_are_independent`: T1 holds B(10) while T2 locks A(20); no abort.
  - `stress_consistent_order`: 8 threads × 100k iterations each lock A(20) then B(10) and bump a shared counter; counter equals 800000.
- [ ] **Step 2:** `test_ranked_mutex_release.cpp`, compiled with `-DNDEBUG`: `static_assert(sizeof(ranked_mutex) == sizeof(std::mutex))`; inverted order B(10) then A(20) runs to completion (checks compiled out); prints `ALL PASS`.
- [ ] **Step 3: Add Make targets and run; expect compile failure** (header missing). `test-locking` builds both tests with the common flags at `-O2` (release test adds `-DNDEBUG`) and runs them; `test-locking-tsan` builds `test_ranked_mutex.cpp` with `-O1 -g -fsanitize=thread` and runs only `stress_consistent_order`, `threads_are_independent`, `cv_any_roundtrip` (select via `argv[1] == "--tsan-subset"`, since fork under TSan is unsupported).
  Run: `make test-locking CXX=~/utils/mamba/envs/gcc13/bin/x86_64-conda-linux-gnu-g++` → fails: `ranked_mutex.hpp: No such file`.
- [ ] **Step 4: Implement `locking/ranked_mutex.hpp`.** `lock()`: scan the held array for the lowest rank; violate if `rank_ >= lowest`; report and `std::abort()`; check capacity; then lock the inner `std::mutex` and append. `unlock()`: find `this` in the array (any position), remove it by moving the last entry into its slot; if absent, report and abort; then unlock. `try_lock()`: no rank check; on success, capacity check and append. Header comment states the rule, the `NDEBUG` behaviour, and that `try_lock` skips the check because it cannot block.
- [ ] **Step 5: Run.** `make test-locking test-locking-tsan CXX=<gcc13>` → both binaries print `ALL PASS`; TSan prints no `WARNING: ThreadSanitizer`.
- [ ] **Step 6: Commit.** `git commit -m "locking: ranked_mutex (rank-checked std::mutex wrapper) with tests"`

### Task 2: ABBA demo, TSan vs rank check

**Files:**
- Create: `locking/examples/abba_demo.cpp`, `locking/examples/check_abba_demo.sh`
- Modify: `Makefile` (target `demo-locking`)

**Interfaces:**
- Consumes: `ranked_mutex` (Task 1).
- Produces: `build/abba_tsan` (`std::mutex`, `-O1 -g -fsanitize=thread`) and `build/abba_ranked` (`ranked_mutex`, checked, `-O2`), each taking one argument `good|bad|both`.

- [ ] **Step 1: Write `check_abba_demo.sh`** asserting, with output saved to `build/abba-*.txt` for the post:
  - `abba_tsan good` and `abba_tsan bad`: exit 0, output has no `lock-order-inversion`.
  - `abba_tsan both`: output contains `lock-order-inversion`.
  - `abba_ranked good`: exit 0.
  - `abba_ranked bad` and `abba_ranked both`: killed by SIGABRT, stderr contains `acquiring "A" (rank 20) while holding "B" (rank 10)`.
  - Prints `DEMO OK` only if all hold.
- [ ] **Step 2: Run `make demo-locking`; expect failure** (no demo source).
- [ ] **Step 3: Implement `abba_demo.cpp`.** Two mutexes A (rank 20) and B (rank 10), type chosen at compile time (`-DUSE_RANKED`). `good` runs a thread that locks A then B; `bad` a thread that locks B then A; `both` runs `good`'s thread, joins it, then `bad`'s thread (the probe's shape, `~/tmp/tsan-probe.07On/abba.cpp`).
- [ ] **Step 4: Run** `make demo-locking CXX=<gcc13>` → `DEMO OK`.
- [ ] **Step 5: Commit.** `git commit -m "locking: ABBA demo, TSan sees both-orders only, rank check fires on the bad order alone"`

### Task 3: Queues take a `Mutex`; benchmark `--mutex`

**Files:**
- Create: `queue/examples/ranked_slot.hpp`
- Modify: `queue/examples/mutex_queue.hpp:12-14`, `queue/examples/two_lock_queue.hpp:18-21`, `queue/examples/queue.hpp` (include + comment row), `queue/examples/test_queue.cpp`, `queue/benchmarks/bench_queue.cpp`, `Makefile` (binaries `build/bench_lock_release`, `build/bench_lock_checked`, `build/bench_lock_debug`)

**Interfaces:**
- Consumes: `ranked_mutex(int, const char*)` (Task 1).
- Produces:
  - `template <typename M> struct RankedSlot : M` with `RankedSlot(int rank, const char* name)`: constructs `M(rank, name)` when `std::is_constructible_v<M, int, const char*>`, else default-constructs `M`. Usable wherever `M` is (Lockable). Does not include `ranked_mutex.hpp`.
  - `template <typename T, typename Mutex = std::mutex> class MutexQueue` / `TwoLockQueue`, with members `RankedSlot<Mutex> _m{10, "queue"}`; `_t{20, "queue tail"}`, `_h{10, "queue head"}`.
  - `bench_queue --mutex {std|ranked}` (default `std`). With `ranked`, only `--variant MutexQueue|TwoLockQueue` are valid (others: usage error, exit 2) and rows report variants `MutexQueueRanked` / `TwoLockQueueRanked`; CSV schema unchanged.

- [ ] **Step 1: Extend `test_queue.cpp`.** Add `MutexQueue<int64_t, ranked_mutex>` and `TwoLockQueue<int64_t, ranked_mutex>` to the int64 FIFO and MPMC (4x4, 1x8, 8x1) cases (checked build: proves the queues never violate their own ranks). Add a compile-time check that `sizeof(MutexQueue<int64_t>)` and `sizeof(TwoLockQueue<int64_t>)` equal the sizes recorded from `main` before the change (record them first by compiling a two-line probe on `main`).
- [ ] **Step 2: Run `make test-queue`; expect compile failure** (`MutexQueue` takes one template argument).
- [ ] **Step 3: Implement** `ranked_slot.hpp`, the two template parameters and members, and `--mutex` in `bench_queue.cpp` (alias templates `MutexQueueRanked<T> = MutexQueue<T, ranked_mutex>`, likewise for `TwoLockQueue`, dispatched only for `--mutex ranked`).
- [ ] **Step 4: Run** `make test-queue test-queue-tsan test-queue-asan bench-queue-smoke CXX=<gcc13>` → all `ALL PASS`, smoke `315 rows, 0 not OK`; and `bash scripts/test_run_part3.sh` → `ALL PASS`. Then build the three `bench_lock_*` binaries and run `build/bench_lock_checked --payload int64 --mutex ranked --variant TwoLockQueue --mix 4x4 --iters 2000 --runs 1` → one row `TwoLockQueueRanked,...,OK`.
- [ ] **Step 5: Commit.** `git commit -m "queue: Mutex template parameter via RankedSlot; bench --mutex std|ranked; lock benchmark builds"`

### Task 4: Runner and tables

**Files:**
- Create: `scripts/run_locking.sh`, `scripts/test_run_locking.sh`
- Modify: `scripts/tables.py` (add `--variants`), `scripts/test_tables.py`

**Interfaces:**
- Consumes: the three `bench_lock_*` binaries and `--mutex` (Task 3).
- Produces:
  - `scripts/run_locking.sh` → for each build in `release checked debug`, one CSV `results/locking/<host>-<sha>-<build>.csv` plus `.meta`, rows for every (mutex valid for that build per the spec matrix, variant, mix), one process each, `CELL_TIMEOUT` and `TIMEOUT`/`CRASH(rc)` handling as in `run_part3.sh`, resume only when rows and `.meta` match. Env: `RESULTS_DIR`, `ITERS`, `RUNS`, `CELL_TIMEOUT`, `BIN_DIR` (default `build`).
  - `tables.py --variants A,B,...` overrides the default five-column list.
- [ ] **Step 1: Write `test_run_locking.sh`** (stub binaries in a temp `BIN_DIR`, reusing `scripts/testdata/stub_bench.sh`): release CSV has 16 rows, checked 8, debug 16; an identical rerun skips all three; `ITERS=5` writes new files. Add a `tables.py` test: `--variants MutexQueue,MutexQueueRanked` yields exactly those two columns.
- [ ] **Step 2: Run both test scripts; expect failure.**
- [ ] **Step 3: Implement** `run_locking.sh` (structure borrowed from `run_part3.sh`; no shared library, the two are small) and `--variants`.
- [ ] **Step 4: Run** `bash scripts/test_run_locking.sh && python3 -I scripts/test_tables.py && bash scripts/test_run_part3.sh` → all pass.
- [ ] **Step 5: Commit.** `git commit -m "scripts: run_locking.sh (release/checked/debug), tables --variants"`

### Task 5: Codegen and sources

**Files:**
- Create: `results/locking/ce/` (request/response JSON per listing, `links.md`), `results/locking/sources.md`

- [ ] **Step 1: Compiler Explorer.** One source pasting `ranked_mutex.hpp` plus `void with_std(std::mutex& m, int& x)` and `void with_ranked(sync_bench::ranked_mutex& m, int& x)`, each `std::lock_guard` + `++x`. Compile with `g134` at `-std=c++17 -O2 -DNDEBUG` and at `-O3 -DNDEBUG` (expect the two functions' bodies identical apart from the name at each level), and at `-std=c++17 -O3` (checked `with_ranked`, to count the check's instructions). Create short links; round-trip each (fetch `/api/shortlinkinfo`, recompile, code 0, identical asm). Record links, the identical-or-not verdict, and instruction counts in `links.md`.
- [ ] **Step 2: Sources.** Against jdk-27-ga, record pinned URLs for `mutex.hpp` and `mutexLocker.cpp`, and whether `Mutex::try_lock()` performs a rank check (quote the lines). Record one primary source each for Linux lockdep (kernel docs at a release tag), `absl::Mutex` deadlock detection (abseil header at a release tag), and Clang `-Wthread-safety` `ACQUIRED_BEFORE`/`ACQUIRED_AFTER` (Clang docs for a pinned release), each with one sentence on what it catches and when. Write all to `sources.md`.
- [ ] **Step 3: Commit.** `git commit -m "results: locking CE listings and pinned sources"`

### Task 6: Xeon runs

- [ ] **Step 1:** On `xeongoldgc01` (check `uptime` first), build the three binaries with GCC 13.4.0 and run `make test-locking demo-locking test-queue`. Then `CELL_TIMEOUT=1800 scripts/run_locking.sh` inside `tmux new -d -s locking`.
- [ ] **Step 2:** Verify three CSVs with 16/8/16 rows and no `FAIL`, `TIMEOUT` or `CRASH`. Commit: `git commit -m "results: locking benchmark, Xeon"`.

### Task 7: ARM runs (two instances)

- [ ] **Step 1:** Start two workers via the `start-worker` skill: label `ostack-ub2404-16x64-arm64`, purpose `queue research`, 6-hour holds with different `DURATION` values (identical requests get merged by Jenkins). Do not auto-terminate them.
- [ ] **Step 2:** On each: wait for boot-time package updates to finish (`pgrep -x apt` / `unattended-upgr` empty), then `sudo apt-get update --allow-releaseinfo-change && sudo apt-get install -y g++-13 make rsync`; rsync the repo (excluding `build`, `results/arm`); run `make test-locking demo-locking test-queue CXX=g++-13`; record `lscpu`, `free -g`, glibc and GCC versions to `results/locking/<host>-machine.txt`; start `CELL_TIMEOUT=1800 scripts/run_locking.sh` in tmux.
- [ ] **Step 3:** Copy results back to `results/locking/arm/w1`, `w2`; verify row counts and no failures; compare instances cell by cell, marking any gap under 20 percent that does not appear on both instances as not claimable. Commit: `git commit -m "results: locking benchmark, two ARM instances"`.

### Task 8: Draft, review, local commit (stop before publishing)

**Files:**
- Create: `_drafts/your-mutex-doesnt-know-it-can-deadlock.md` (blog repo)

- [ ] **Step 1:** Write the post per the spec's post shape (2,500-3,000 words), title "Your Mutex Doesn't Know It Can Deadlock". Demo output from `build/abba-*.txt`; tables via `tables.py --variants`; CE listings and links from Task 5; prior art and HotSpot links only from `sources.md`; the `try_lock` comparison with HotSpot only as Task 5 Step 2 found it. Leave `<!-- AUTHOR: ... -->` where a personal detail belongs instead of inventing one. Footer: repo, Xeon and ARM hardware, flags, matrix, previous link to Queues Part 3 (`{% post_url 2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue %}`).
- [ ] **Step 2:** Fact-check every number in the draft against the CSVs; local Jekyll build with `--drafts` (see memory note "Local Jekyll build"), no Liquid errors.
- [ ] **Step 3:** Run `/review-blog` on the draft; apply must-fix and should-fix items.
- [ ] **Step 4:** Commit locally in both repos. **Stop.** List remaining `<!-- AUTHOR -->` notes for the user. Publishing (date, social card, push of both repos) waits for the user's explicit OK.
