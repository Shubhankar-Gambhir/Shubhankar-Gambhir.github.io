# Queues Part 3: Payload Size -- Design

Date: 2026-10-31
Status: approved 2026-10-31

## Intent

Part 3 of the concurrent-queue sub-series on the blog. Part 1 ("A Deep Dive into Producer-Consumer Queues", 2026-07-07) and Part 2 ("Lock-Free Is Not Free", 2026-08-19) moved only `int64_t`. Part 2 closed with a promise:

> Every queue here moves an `int64_t` [...] it flatters the linked variants, which hand around pointers to nodes, and penalizes the ring, which copies values into cells. A payload that costs real money to copy should move the ranking, possibly a lot. That is the next thing to measure.

Part 3 keeps that promise and corrects its premise. The linked variants do not hand around pointers. Every variant in the lab copies the payload into queue-owned storage on push (`const T&` cannot be moved from) and moves it out on pop. For a trivially copyable payload the move is also a byte copy. So every variant pays the same two N-byte transfers; what differs is **where** each transfer happens and what is held while it runs.

**Thesis (user's choice, "B"):** it is not how many copies a queue makes, it is where they happen -- inside a critical section, outside it, or while holding a ring cell other threads are waiting on.

This fits the blog's recurring shape: measure the folk explanation, find the cost one layer lower, and correct the previous post in public (Part 2 opened with "A Correction to Part 1"; Part 3 opens with a correction to Part 2).

## Goals

- Measure how throughput changes with payload size for five variants that sit at different points on the "where is the copy" axis.
- Show the thesis directly with one new variant (`TwoLockQueueDeferred`) that moves the pop-side copy out of the lock, and one interface change (`push(T&&)`) that moves the push-side cost off the queue entirely for `std::string`.
- Explain at least the headline effects with a stated mechanism, using Compiler Explorer listings of the copy code where the codegen changes with N.
- Produce a crossover table (which variant wins at which N, per thread mix) as the evidence for the thesis.

## Non-goals

- The pool variants (`MutexLinkedQueuePooled`, `TwoLockQueuePooled`, `TwoLockQueueSharded`, `TwoLockQueueLFPool*`). They answered Part 1's allocator question; the user said "don't repeat."
- An in-place / Disruptor-style variant (user: skip it, no closing sketch either).
- Retrofitting assembly analysis into Parts 1 and 2.
- A forward link to the HotSpot-for-C++ series (user: revisit later).
- A buyer's guide ("above N bytes use X"). The crossover table is evidence, not the headline.

## Anti-goals

- Asserting a cross-variant gap on ARM that is small (under roughly 2x) or has no stated mechanism. Part 2 had to walk back a Part 1 claim because rented ARM instances of the same CPU model disagree.
- Results dominated by something the post does not explain (page faults from an exploding backlog, the harness's own per-item work) and then attributed to copy placement.
- A benchmark that lets the compiler skip part of the copy.

## Constraints

### Setup: identical to Parts 1 and 2 (user)

- Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (xeongoldgc01, 2x16 cores, two NUMA nodes, 187 GB RAM) and ARM Neoverse-N1 (16 cores, single NUMA node, Jenkins `devops/start-a-worker`, terminate when done).
- Compilers: GCC 13.4.0 (x86, `~/utils/mamba/envs/gcc13`), GCC 13.3.0 (ARM).
- Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, no `-march`, no alignment flags. This departs from the blog CLAUDE.md's "always use `-falign-functions=64 -falign-loops=64`" rule, deliberately, so numbers compare to Parts 1-2. The post's methodology footer says so.
- The nine thread mixes from `bench_queue.cpp` (`1x1, 2x2, 4x4, 8x8, 16x16, 1x8, 8x1, 1x15, 15x1`), consecutive-core-id pinning, synchronized start, 1M items per producer, 5 runs after warmup, median reported, ns/item.
- ARM: two separate instances; only claim gaps that are large and have a mechanism (see anti-goals). ARM runs the `std::array` sweep only (user); the `std::string` and `push(T&&)` configurations are Xeon-only, and the post says so.

### Code

- Companion repo: `~/tmp/cpp-sync-benchmark` (GitHub: `concurrent-data-structures-cpp`), `queue/` directory (variants in `queue/examples/`, harness in `queue/benchmarks/bench_queue.cpp`).
- Claude writes all code, runs and the draft (user's explicit call for this part; overrides the usual scaffold-only rule).
- Existing variants are used **unchanged**, so `int64_t`-sized results stay comparable to Part 2. Harness changes are allowed.
- No `Co-Authored-By: Claude` line in commits to this repo or the blog.

### Blog conventions (from the blog CLAUDE.md)

No em dashes; pinned compiler/stdlib versions with tagged source links; `{% post_url %}` for internal links; AT&T syntax with every instruction annotated, in ` ```nasm ` fences; a Compiler Explorer link for every assembly snippet, each round-tripped through the CE API to confirm it compiles; methodology section; footer with companion repo, hardware, previous/next links; `/review-blog` before publishing. Target 2500-3000 words.

## Decided design

### Variants (five)

The decision axis is what is held while each N-byte transfer runs. Read from the current code:

| Variant | Push-side copy | Pop-side move | Notes |
|---|---|---|---|
| `MutexQueue` | inside the single lock (`std::queue::push`) | inside the single lock | libstdc++ deque: once `sizeof(T) >= 512`, each element gets its own chunk (`__deque_buf_size`, GCC 13 `stl_deque.h`), so push also calls `malloc` inside the lock |
| `MutexLinkedQueue` | outside (`new NodeNA(v)` before locking) | inside the single lock, so the move also blocks producers | |
| `TwoLockQueue` | outside | inside the head lock only, so it blocks other consumers but not producers | |
| `TwoLockQueueDeferred` (new) | outside | **outside** any lock | The test of the thesis; see below |
| `LockFreeRing<T,1024>` | no lock, but runs while holding a claimed cell | same | Cells hold values inline: at N=4096 the ring is about 4 MB |

`LockFreeRing<65536>` is dropped: at N=4096 it would need about 256 MB per queue, and Part 2 already covered capacity.

### `TwoLockQueueDeferred`

Identical to `TwoLockQueue` except that pop releases the head lock **before** moving the value out. The hazard: after unlocking, the node holding the value (`n`, which becomes the new dummy) can be unlinked and freed by the next consumer while this one is still reading it. The variant must make freeing a node wait until both of these have happened: (1) its value has been moved out and (2) it has been unlinked as the old dummy. The mechanism is the builder's choice (a two-count atomic where the last party deletes avoids any spinning), but it must be:

- free of data races under TSan, with `make test-queue` extended to cover the new variant;
- documented in a header comment that explains the lifetime rule, matching the style of the existing variant headers;
- added to `queue.hpp`'s variant list.

### Payloads (two)

- `std::array<char, N>` for N = 8, 64, 256, 512, 1024, 2048, 4096. 512 and 2048 bracket two known thresholds: the libstdc++ deque chunk rule (512) and glibc's tcache limit (requests up to 1032 bytes), which linked nodes cross between N=1024 and N=2048.
- `std::string` of length N for the same N values (user). N=8 is inside libstdc++'s small-string buffer (15 chars), so it allocates nothing; that is an expected data point, not an outlier.

With `std::string`, `push(const T&)` deep-copies (allocation plus N bytes) and `pop`'s move transfers about 32 bytes, so the whole cost lands on the push side. With `std::array` both sides cost N bytes.

### `push(T&&)` configuration (my call)

For the `std::string` payload only, an extra configuration in which every variant gets a `push(T&&)` overload and the producer moves its string in. Same queue, same payload; only the push signature changes. This shows the interface deciding where the cost lands. To make the comparison fair, the producer **builds a fresh string per item in both configurations** (a real producer has to build its message either way), so `const T&` costs build + copy and `T&&` costs build + move.

Adding `push(T&&)` to the five variants is an additive change; existing `const T&` behaviour stays bit-for-bit the same.

### Harness changes (my calls)

- Construct the queue on the heap (`std::make_unique<Queue>()`). The 4 KB-payload ring is too large for a thread stack.
- Template the benchmark on payload type. The producer writes its sequence number into the payload (first 8 bytes of the array; the string's content for strings); the consumer recovers it for the existing count and checksum invariant.
- Defeat elision without making the consumer do N bytes of work: after each pop, pass `&out` through an empty `asm volatile` with a `"memory"` clobber so the compiler must assume all N bytes are read. Consumer work per item stays constant across N, so a slowdown with N is the queue's, not the harness's.
- Keep the `std::array` producer reusing one local buffer (only the sequence bytes change per item) so producer work is also constant across N. The string producer is the exception described above.
- Output: one machine-readable line per (variant, payload, N, mix) with median/min/max ns/item, saved under `results/` with the machine and commit in the filename, so tables are generated from files and not hand-copied.

### Assembly / Compiler Explorer (user: yes)

CE listings of the pop-side move for `std::array<char,N>` at a few N values, with GCC 13.4.0 x86-64 and the post's flags. The expectation to verify, not assert: without `-march`, codegen goes from scalar `mov`s, to SSE2 16-byte `movdqu` sequences, to a `memcpy` call (or `rep movsb`) as N grows. Use the listing only where it explains a measured step; if no step in the data lines up with a codegen change, say so rather than force it.

### Post shape (my call, open to the user)

1. Hook and the correction to Part 2 (two transfers everywhere; show the push/pop code).
2. The "where" table above.
3. `std::array` sweep: results and crossover table, Xeon then ARM; page faults in the fan-in cells.
4. `TwoLockQueueDeferred`: the lifetime problem, the fix, the result.
5. `std::string`: lopsided cost, SSO at N=8, then `const T&` versus `T&&`.
6. Codegen as N grows (CE listings).
7. What I learned; methodology; footer.

### Baseline check against Part 2 (my call)

Before the payload sweep, re-run `int64_t` through the new harness on the Xeon. If it differs from Part 2's published tables beyond normal run-to-run spread, note it in the post (and use the new-harness baseline in Part 3's tables); it does not block the sweep. The N=8 `std::array` row is compared to this baseline, not to Part 2's numbers.

### Where the work stops (my call)

Code is committed locally in the companion repo; the post is written in `_drafts/` and goes through `/review-blog`. Pushing the companion repo to GitHub and moving the post to `_posts/` + pushing `main` (which deploys publicly) each wait for the user's explicit OK.

## Left to the builder

- The lifetime mechanism in `TwoLockQueueDeferred`, subject to the constraints above.
- Exact output format, plotting (if any), and the script that turns `results/` files into markdown tables.
- Which N values get CE listings.
- Whether `perf stat` counters (e.g. page faults, cache misses) are needed to back a specific mechanism claim; use them where a claim would otherwise be a guess.
- Run order and scheduling on the Xeon; total runtime estimate before the full sweep.

### Backlog in fan-in mixes (user)

Unbounded queues build a backlog when producers outrun consumers. At 15P x 1C with N=4096, the worst case is about 15M items x ~4.1 KB, roughly 60 GB (8P x 1C is affected too). Run as-is, keeping Parts 1-2 semantics. Record page faults with `perf stat` for the large-N fan-in cells and explain them in the post rather than letting them pass as copy cost. On ARM, check the instance's RAM first and skip only the cells that cannot fit; the post lists any skipped cells.

### Checkpoint after the Xeon sweep (user)

After the Xeon sweep (all 21 payload configurations), stop and show the user the results plus a proposed headline, before any ARM instance is started and before drafting. If the data does not back thesis B (for example `TwoLockQueueDeferred` shows no clear gain, or the allocator thresholds dominate), the user re-decides the framing at this point.

## Open risks

- **Runtime.** About 35 billion items on the Xeon (5 variants x 21 payload configurations x 9 mixes x 6 runs x up to 16M items). Time one N=4096 configuration first and report the full-sweep estimate before launching it.
- **ARM variance** (Part 2's lesson): two instances, large gaps only.
- **ARM RAM** is not recorded anywhere; read it from the instance before the ARM sweep.

## Checkpoint decisions (2026-11-03, user)

Xeon sweep findings (`results/diagnostics-heap-trim.md` in the companion repo): copy location is a real but modest effect (TwoLockQueueDeferred vs TwoLockQueue, 4 consumers: 1.2x at 512 B, 1.4x at 1 KB, 1.25x at 2 KB and 1.5x at 4 KB with heap trimming off, zero for `std::string`). The large gaps at N >= 2048 come from glibc heap trimming (bimodal, ~1 page fault per item in the slow mode, dependent on which variants ran earlier in the same process) and, in fan-in, from backlog page faults. The ring wins every mix above 8 bytes because it never allocates.

- **Headline:** "We blamed the copy. It was the allocator, again." Thesis B stays as the honest-sized copy-location effect (TwoLock vs Deferred, with `std::string` as the control); the headline result is the allocator mechanism and the allocation-free ring. Echoes Part 1.
- **Re-run before drafting (Xeon):** N = 1024, 2048, 4096, all three payload configurations, one process per variant, twice: default glibc and with heap trimming off (`GLIBC_TUNABLES=glibc.malloc.trim_threshold=17179869184:glibc.malloc.top_pad=1073741824`). These replace the original sweep's cells at those sizes in the post; N <= 512 cells stay from the original sweep.
- **-O3 spot check:** 4x4 and 15x1 at N = 8, 512, 4096, one process per variant, compared to -O2 run the same way; reported in one methodology sentence.
- ARM still waits until the re-run is reviewed.

## Headline after ARM (2026-11-17, user)

"Two layers": where the copy happens decides whether consumers keep up; the allocator decides what falling behind costs. Copy location and the allocator get equal billing; the ARM 16x16 / 4 KB cell (TwoLockQueue runs out of memory, TwoLockQueueDeferred runs at 889 ns) is the centerpiece; the Xeon-vs-ARM difference is framed as glibc 2.28 vs 2.39 with the architecture confound stated. Supersedes the 2026-11-03 "allocator, again" headline.
