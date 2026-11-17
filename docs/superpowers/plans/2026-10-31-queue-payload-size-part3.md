# Queues Part 3 (Payload Size) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the queue benchmark lab to sweep payload type and size across five variants (one new), run it on the Xeon, stop for the user's checkpoint, then run ARM and draft the post.

**Architecture:** A small payload-traits header lets the existing tests and the benchmark run unchanged variants with `int64_t`, `std::array<char,N>` and `std::string` payloads. One new variant (`TwoLockQueueDeferred`) moves the pop-side move out of the lock using a two-count node lifetime. The benchmark becomes CLI-selectable and emits CSV, so sweeps run in resumable chunks and tables are generated from files.

**Tech Stack:** C++17, GCC 13.4.0 (conda `~/utils/mamba/envs/gcc13`), Make, bash, Python 3 (stdlib only), `perf stat`, Compiler Explorer API.

**Spec:** `docs/superpowers/specs/2026-10-31-queue-payload-size-part3-design.md` (blog repo). Read it before any task.

## Global Constraints

- Companion repo: `~/tmp/cpp-sync-benchmark` (remote `concurrent-data-structures-cpp`). Home is NFS-shared, so the same checkout is visible on `xeongoldgc01.azulsystems.com`.
- Compile flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, no `-march`, no alignment flags (matches Parts 1-2).
- Benchmark binaries are built with `CXX=~/utils/mamba/envs/gcc13/bin/x86_64-conda-linux-gnu-g++` and linked with `-static-libstdc++`, so `std::string`'s out-of-line code is GCC 13.4.0's, not the conda env's libstdc++ 15.2 runtime. (Plan's call; the methodology footer states it.)
- Existing variant behaviour for `push(const T&)` / `pop(T&)` is unchanged; only additive edits (a `push(T&&)` overload, an rvalue node constructor).
- Benchmark constants: 1M items per producer, 5 runs after 1 warmup, median, nine mixes `1x1 2x2 4x4 8x8 16x16 1x8 8x1 1x15 15x1`, consecutive-core pinning.
- Payload sizes N: `8 64 256 512 1024 2048 4096`.
- Every commit: no `Co-Authored-By: Claude` line. Nothing is pushed to any remote in this plan.
- Blog conventions (Task 9): no em dashes, ` ```nasm ` fences with AT&T syntax and every instruction annotated, CE link per assembly snippet round-tripped through the CE API, `{% post_url %}` for internal links, footer with repo/hardware/prev-next.

## Review Focus

- A run that fails the count/checksum invariant must be reported as a failure, never as a fast number (the current harness turns 0.0 s into `0.00 ns/item`). Pinned in Task 3.
- N=8 payloads: the 8-byte sequence stamp exactly fills `std::array<char,8>`, and an 8-char `std::string` lives in the SSO buffer. Both must round-trip. Pinned in Task 1.
- `LockFreeRing<std::array<char,4096>,1024>` on the heap (about 4 MB, `alignas(64)` members) must construct and pass FIFO/MPMC. Pinned in Task 1.
- `TwoLockQueueDeferred` destroyed with items still queued must free every node, with no leak or double free. Pinned in Task 2 (ASan).
- The copy must not be optimized away: at 1x1, `std::array<char,4096>` must be measurably slower than `std::array<char,8>` for every variant. Pinned in Task 3.

---

### Task 1: Payload traits, `push(T&&)` overloads, payload-generic tests

**Files:**
- Create: `queue/examples/payload.hpp`
- Modify: `queue/examples/nodes.hpp` (add rvalue constructors to `Node` and `NodeNA`)
- Modify: `queue/examples/mutex_queue.hpp`, `mutex_linked_queue.hpp`, `two_lock_queue.hpp`, `lock_free_ring.hpp` (add `bool push(T&& v)`)
- Modify: `queue/examples/test_queue.cpp`

**Interfaces:**
- Produces, in `payload.hpp`, namespace `sync_bench`:
  - `template <size_t N> using Bytes = std::array<char, N>;`
  - `template <typename T> struct Payload;` with specializations for `int64_t`, `Bytes<N>`, `std::string`, each providing
    `static T make(int64_t seq, size_t n)` (n ignored for `int64_t`/`Bytes`; string of length `n`, filled with `'x'`),
    `static void stamp(T& p, int64_t seq)`,
    `static int64_t seq(const T& p)`.
    Array and string store `seq` in the first 8 bytes via `std::memcpy`; `static_assert(N >= 8)` for `Bytes`, `assert(n >= 8)` for strings.
  - `std::string` payloads are heap-free at N=8 (SSO); no special-casing needed.
- Produces on each of the four variants: `bool push(T&& v)` with the same return semantics as `push(const T&)`.
- Produces in `nodes.hpp`: `explicit Node(T&& v)`, `explicit NodeNA(T&& v)`.

- [ ] **Step 1: Make `test_fifo` / `test_mpmc` payload-generic and add the new cases.** The variants expose no `value_type`, so the payload is a second template parameter: `template <typename Queue, typename T> void test_fifo(const char* name, int64_t K, size_t n = 8, bool use_move = false)` and the same extra parameters on `test_mpmc`. Build each item with `Payload<T>::make(i, n)`; check FIFO order with `Payload<T>::seq(out) == i`; the MPMC sum adds `Payload<T>::seq(x)`. `use_move` makes producers call `q.push(std::move(item))`. Construct every queue with `auto q = std::make_unique<Queue>();` (the stack is too small for the 4 KB ring). Keep every existing `int64_t` call, now spelled `test_fifo<Q, int64_t>`. Add, for each of `MutexQueue`, `MutexLinkedQueue`, `TwoLockQueue`, `LockFreeRing<T,1024>`:
  - `Bytes<8>` and `Bytes<4096>`: FIFO (K=1'000 for the ring, 10'000 otherwise) and MPMC 4x4 x 20'000;
  - `std::string` with n=8 and n=4096: the same FIFO + MPMC, once with `use_move=false` and once with `use_move=true`. Assertions are on queue output only; the producer's moved-from string is not checked.

- [ ] **Step 2: Build and run; expect compile failure.** Run: `make test-queue` → fails on `Payload` not declared / no `push(T&&)`.

- [ ] **Step 3: Write `payload.hpp`, the rvalue node constructors and the four `push(T&&)` overloads.** Each overload mirrors its `const T&` sibling with `std::move(v)` at the single copy site (`_q.push`, `new Node<T>(std::move(v))`, `new NodeNA<T>(std::move(v))`, `c->value = std::move(v)`). Add `payload.hpp` to `queue.hpp`'s include list and header comment.

- [ ] **Step 4: Run tests.** Run: `make test-queue CXX=~/utils/mamba/envs/gcc13/bin/x86_64-conda-linux-gnu-g++` → `ALL PASS (0 failures)`, no warnings.

- [ ] **Step 5: Commit.** `git commit -m "queue: payload traits, push(T&&) overloads, payload-generic tests"`

### Task 2: `TwoLockQueueDeferred` plus TSan and ASan targets

**Files:**
- Create: `queue/examples/two_lock_queue_deferred.hpp`
- Modify: `queue/examples/queue.hpp` (include + comment row), `queue/examples/test_queue.cpp`, `Makefile`

**Interfaces:**
- Consumes: `Payload<T>`, `Bytes<N>` from Task 1.
- Produces: `template <typename T> class sync_bench::TwoLockQueueDeferred` with `bool push(const T&)`, `bool push(T&&)`, `bool pop(T&)`, unbounded, same contract as `TwoLockQueue`.
- Produces Make targets `test-queue-tsan` and `test-queue-asan` (build `test_queue.cpp` with `-fsanitize=thread -O1 -g` / `-fsanitize=address,undefined -O1 -g` into `build/test_queue_tsan`, `build/test_queue_asan`).

- [ ] **Step 1: Add tests.** In `test_queue.cpp`, run the full set of Task 1 cases (int64 FIFO/MPMC 4x4, 1x8, 8x1; `Bytes<8>`, `Bytes<4096>`; string n=8/4096 copy and move) for `TwoLockQueueDeferred`. Add `test_destroy_nonempty<Queue, T>(name, K)`: push K=1'000 items, pop 10, let the queue go out of scope; passes if it returns (ASan supplies the leak / double-free check).

- [ ] **Step 2: Run; expect compile failure** (`TwoLockQueueDeferred` not declared).

- [ ] **Step 3: Implement `two_lock_queue_deferred.hpp`.** Private node type with `T value{}`, `std::atomic<NodeD*> next{nullptr}`, `std::atomic<int> refs{2}`. `push` is `TwoLockQueue::push`. The lifetime rule, which the header comment must explain:

```cpp
// A node is freed only after BOTH: (a) its value has been moved out, and
// (b) it has been unlinked as the old dummy. Each event drops one ref; the
// party that drops the last one deletes. The initial dummy starts at refs=1
// (it has no value to read).
bool pop(T& out) {
    NodeD *n, *old;
    {
        std::lock_guard l(_h);
        n = head->next.load(std::memory_order_acquire);
        if (n == nullptr) return false;
        old = head;
        head = n;
    }
    out = std::move(n->value);   // outside the lock: the whole point
    release(n);                  // event (a) for n
    release(old);                // event (b) for old
    return true;
}
static void release(NodeD* x) {
    if (x->refs.fetch_sub(1, std::memory_order_acq_rel) == 1) delete x;
}
```

  Destructor: `T out; while (pop(out)); delete head;` (the final dummy has had its value read; only its unlink ref remains, and nobody else will drop it). Add the row to `queue.hpp`.

- [ ] **Step 4: Add the two Make targets**, mirroring `build/test_queue`'s rule.

- [ ] **Step 5: Run all three.** `make test-queue test-queue-tsan test-queue-asan CXX=<gcc13>` → each prints `ALL PASS`; TSan prints no `WARNING: ThreadSanitizer`; ASan/LSan print nothing. If TSan or ASan cannot link with the conda compiler, use the system `g++` (11.4.0) for the sanitizer builds only and say so in the commit message. If TSan reports on a **pre-existing** variant, do not fix it: record the report in `results/tsan-preexisting.txt` and raise it with the user at the checkpoint.

- [ ] **Step 6: Commit.** `git commit -m "queue: TwoLockQueueDeferred (value moved out after unlock) + TSan/ASan targets"`

### Task 3: Payload-generic, CLI-driven benchmark with CSV output

**Files:**
- Modify: `queue/benchmarks/bench_queue.cpp`
- Modify: `Makefile` (`build/bench_queue` links `-static-libstdc++`)

**Interfaces:**
- Consumes: Task 1 `Payload<T>`/`Bytes<N>`, Task 2 `TwoLockQueueDeferred`.
- Produces the CLI used by Tasks 4-8:
  `build/bench_queue --payload {int64|array|string|string-move} [--n N] [--variant NAME] [--mix PxC] [--iters K] [--runs R]`
  - `--n` required unless `--payload int64`; one of `8 64 256 512 1024 2048 4096`.
  - `NAME` ∈ `MutexQueue MutexLinkedQueue TwoLockQueue TwoLockQueueDeferred LockFreeRing1024`; omitted → all five.
  - `--mix` omitted → all nine. `--iters` default 1'000'000, `--runs` default 5 (both only lowered for smoke tests).
- Produces CSV on stdout, header printed once:
  `variant,payload,n,push,producers,consumers,median_ns,min_ns,max_ns,status`
  where `push` is `copy` or `move`, ns are per item, `status` is `OK` or `FAIL`. Exit code 1 if any row is `FAIL`.

- [ ] **Step 1: Write the smoke check as a Make target `bench-queue-smoke`** that runs, with `--iters 2000 --runs 1`, every payload (`int64`; `array`, `string`, `string-move` at n=8 and n=4096) across all variants and mixes, and fails unless every data row ends in `,OK`. Add `bench-queue-elision` that runs `--payload array --mix 1x1 --iters 200000 --runs 3` at n=8 and n=4096 and fails unless, for every variant, `median_ns(4096) > 2 * median_ns(8)` (a small `awk` in the recipe is fine).

- [ ] **Step 2: Run `make bench-queue-smoke`; expect failure** (unknown options).

- [ ] **Step 3: Rewrite the harness.**
  - `template <typename Queue, typename T, bool Move> double bench_one(int producers, int consumers, long iters, size_t n)`; queue built with `std::make_unique<Queue>()`.
  - Producer: for `std::string`, `T item = Payload<T>::make(i, n);` per item, then `q.push(item)` or `q.push(std::move(item))` by `Move` (on a full ring, retry with the same object; a moved-from string is only moved once the push succeeds, because `push(T&&)` only moves on the success path, which holds for all five variants). For `int64_t` and `Bytes<N>`, one local `T` built once with `make`, then `Payload<T>::stamp(item, i)` each iteration and `q.push(item)`.
  - Consumer: after each successful `pop(x)`, `asm volatile("" : : "r"(&x) : "memory");` then add `Payload<T>::seq(x)` to the local sum.
  - Keep the existing count/checksum invariant; on mismatch the run is `FAIL` (do not compute ns from it). A cell is `FAIL` if any of its runs fails.
  - Dispatch: runtime `--n` maps to compile-time `Bytes<N>` via a fixed switch over the seven sizes; variant names map to templates via a fixed table. Keep `pin_thread`, `spawn_on`, the synchronized start and the warmup exactly as they are.

- [ ] **Step 4: Run `make bench-queue-smoke bench-queue-elision CXX=<gcc13>`** on the Xeon (`ssh xeongoldgc01.azulsystems.com 'cd ~/tmp/cpp-sync-benchmark && ...'`) → both pass. Also confirm the binary uses no libstdc++.so: `ldd build/bench_queue | grep -c libstdc++` → `0`.

- [ ] **Step 5: Commit.** `git commit -m "bench_queue: payload/size/variant CLI, heap queues, elision guard, CSV output"`

### Task 4: Sweep, perf, and table scripts

**Files:**
- Create: `scripts/run_part3.sh`, `scripts/perf_fanin.sh`, `scripts/tables.py`, `scripts/test_tables.py`, `scripts/fixtures/sample.csv`

**Interfaces:**
- Consumes: the Task 3 CLI and CSV schema.
- Produces:
  - `scripts/run_part3.sh {baseline|array|string|string-move|all} [--variant NAME]` → one CSV per (payload, n) chunk at `results/<hostname>-<git short sha>-<payload>-n<N>.csv`; skips chunks whose file already exists and ends in a complete set of rows (resumable); logs to `results/<hostname>-run.log`.
  - `scripts/perf_fanin.sh` → for mixes `8x1` and `15x1`, N ∈ {1024, 2048, 4096}, all five variants, `array` payload, runs `perf stat -e page-faults,dTLB-load-misses,cache-misses -x,` around a `--runs 1` invocation and writes `results/<hostname>-<sha>-perf-fanin.csv` (columns: variant,n,mix,page_faults,dtlb_misses,cache_misses,ns_per_item).
  - `scripts/tables.py <csv>... --by {mix|n} [--payload P] [--machine-label L]` → markdown tables on stdout: rows = mix (or N), columns = variants in the fixed order `MutexQueue, MutexLinkedQueue, TwoLockQueue, TwoLockQueueDeferred, LockFreeRing1024`, cells = median ns with one decimal, fastest cell in each row bolded; `FAIL` cells printed as `FAIL`.

- [ ] **Step 1: Write `scripts/test_tables.py`** (stdlib `unittest`) against `scripts/fixtures/sample.csv` (two mixes x five variants, one `FAIL` row): asserts the header row lists the five variants in order, the fastest cell per row is wrapped in `**`, and the `FAIL` cell renders as `FAIL`.
- [ ] **Step 2: Run** `python3 -I scripts/test_tables.py` → fails (no `tables.py`).
- [ ] **Step 3: Write the three scripts.**
- [ ] **Step 4: Run** `python3 -I scripts/test_tables.py` → OK. Dry-run `scripts/run_part3.sh baseline` with `ITERS=2000 RUNS=1` env overrides (the script passes them through as `--iters/--runs`) on the Xeon and confirm the CSV appears and a second invocation skips it.
- [ ] **Step 5: Commit.** `git commit -m "scripts: resumable Part 3 sweep, fan-in perf stat, CSV->markdown tables"`

### Task 5: Xeon runs

All on `xeongoldgc01`, inside `tmux new -d -s part3 ...`, output under `results/`. Nothing else heavy runs on the box meanwhile (check `uptime` first).

- [ ] **Step 1: Baseline.** `scripts/run_part3.sh baseline` (int64, all five variants). Compare `MutexQueue`, `TwoLockQueue` and `LockFreeRing1024` against Part 2's published Xeon table (`_posts/2026-08-19-...md`, table at the "Mix (Xeon)" header). Write the comparison to `results/baseline-vs-part2.md`: per cell, Part 2 value, new value, ratio. A mismatch is noted, not blocking.
- [ ] **Step 2: Timing probe.** Time `--payload array --n 4096` (all variants, all mixes). Extrapolate the full 21-configuration sweep and **report the estimate to the user** before Step 3. If it exceeds 24 h, ask the user before continuing.
- [ ] **Step 3: Full sweep.** `scripts/run_part3.sh all`. Verify: every expected CSV exists, `grep -c ',FAIL' results/*.csv` → 0 for every file.
- [ ] **Step 4: Fan-in perf.** `scripts/perf_fanin.sh`.
- [ ] **Step 5: Commit results.** `git add results/ && git commit -m "results: Part 3 Xeon sweep"`

### Task 6: Compiler Explorer listings

**Files:**
- Create: `results/ce/` (one JSON request + response per listing), `results/ce/links.md`

- [ ] **Step 1:** For `std::array<char,N>` move-assignment at N ∈ {8, 64, 256, 4096} (a standalone function `void mv(Bytes<N>& out, Bytes<N>& in) { out = std::move(in); }`), compile via the CE API (`POST https://godbolt.org/api/compiler/g134/compile`, GCC 13.4 x86-64; confirm the compiler id with `GET /api/compilers/c++` first) with the post's flags, AT&T output. Save request/response.
- [ ] **Step 2:** Create short links (`POST /api/shortener`) and round-trip each: fetch the short link's state and recompile it; it must compile with zero errors and produce the saved assembly. Record links and the observed codegen per N in `links.md`.
- [ ] **Step 3:** Note which, if any, measured steps in Task 5's data line up with a codegen change. If none, say so in `links.md`.
- [ ] **Step 4: Commit.** `git commit -m "results: CE listings for array payload moves"`

### Task 7: CHECKPOINT with the user (stop here)

- [ ] **Step 1:** Generate the array, string and string-move tables with `tables.py` (by mix and by N), plus the perf fan-in table and the baseline comparison.
- [ ] **Step 2:** Present to the user: the headline numbers, whether `TwoLockQueueDeferred` beats `TwoLockQueue` and where, the 512 / ~1 KB thresholds if visible, the fan-in page-fault picture, any TSan reports on pre-existing variants (Task 2), and a proposed headline (thesis B, or a reframe if the data says otherwise).
- [ ] **Step 3:** Wait for the user's decision on framing. Do not start ARM or the draft before it.

### Task 8: ARM runs (after checkpoint)

- [ ] **Step 1:** Start a Neoverse-N1 worker via the `start-worker` skill (PURPOSE=ZVM-36508). Record `free -g`, `nproc`, `lscpu` model. Build with GCC 13.3.0 on the worker, same flags, `-static-libstdc++`; run `make test-queue bench-queue-smoke`.
- [ ] **Step 2:** From RAM, compute which fan-in cells cannot fit (worst-case backlog bytes ≈ `producers × 1M × (N + 32)`, keep under 80% of RAM) and pass `--variant`/`--mix` exclusions; list skipped cells in `results/<host>-skipped.txt`.
- [ ] **Step 3:** `scripts/run_part3.sh array` in tmux; verify no `FAIL`; copy results back; **terminate the worker** (abort its `start-a-worker` build).
- [ ] **Step 4:** Repeat Steps 1-3 on a second, fresh instance.
- [ ] **Step 5:** Compare the two instances cell by cell; mark any cross-variant gap under 2x, or one that flips between instances, as not claimable. Commit results.

### Task 9: Draft, review, local commit (stop before publishing)

**Files:**
- Create: `_drafts/queues-part-3-payload-size.md` (blog repo; working title per the user's checkpoint decision)

- [ ] **Step 1:** Write the post per the spec's outline as amended at the checkpoint, 2500-3000 words, tables from `tables.py`, CE links from `results/ce/links.md`, methodology footer stating: flags, `-static-libstdc++`, GCC 13.4.0 / 13.3.0, no alignment flags (deliberate, for comparability), ARM array-only and two instances, skipped cells, baseline comparison.
- [ ] **Step 2:** Build locally (see memory note "Local Jekyll build": mamba ruby + gcc14 on PATH, gems in `~/tmp/blog-vendor`) with drafts enabled; the page renders, no Liquid errors.
- [ ] **Step 3:** Run `/review-blog` on the draft; apply fixes.
- [ ] **Step 4:** Commit locally in both repos. **Stop.** Pushing the companion repo and publishing the post each need the user's explicit OK.
