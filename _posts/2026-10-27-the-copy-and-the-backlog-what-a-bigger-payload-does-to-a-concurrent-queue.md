---
title: "The Copy: What a Bigger Payload Does to a Concurrent Queue"
date: 2026-10-27
categories: [C++, Performance]
tags: [concurrency, queue, payload, memcpy, glibc, malloc, page-faults, ring-buffer, benchmarks]
description: >-
  Every queue in the lab copies its payload twice, from 8 bytes up to 4 KB. On a
  two-socket Xeon, moving one of those copies out of a two-lock queue's head lock is worth
  1.54x at 4 KB, but only once glibc 2.28's allocator is held still: with default settings
  it flipped the same cell between fast and seven times slower from one process to the next.
image:
  path: /assets/img/og/the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue.png
  alt: "The Copy and the Backlog: What a Bigger Payload Does to a Concurrent Queue"
  hero: false
---

At sixteen producers, sixteen consumers and 4 KB items, one of the queues in this experiment reached 64.6 GB of resident memory in 38 seconds before the kernel's OOM killer took it. [The follow-up post]({% post_url 2026-10-28-the-backlog-what-falling-behind-costs-an-unbounded-queue %}) explains why; this one starts with a much smaller question.

The [previous post]({% post_url 2026-08-21-the-ring-that-doesnt-allocate-benchmarking-a-bounded-mpmc-queue %}) ended with a prediction. All the measurements so far moved an `int64_t`, which "flatters the linked variants, which hand around pointers to nodes, and penalizes the ring, which copies values into cells." A payload that costs real money to copy should move the ranking. So I made the payload expensive, from 8 bytes up to 4 KB, and measured five queues on the same two machines.

The ranking moved, but not the way Part 2 said it would, and my first reading of the new numbers was wrong too. The first sweep made copy location look like a fivefold effect. Most of that was glibc's allocator, which on the Xeon swung the same cell between fast and seven times slower from one process to the next. Hold the allocator still and a smaller copy effect is left. This post stays on the Xeon. On the ARM machines' newer glibc the allocator stops being a coin flip, and that copy effect shows through on its own, which is where [the next one]({% post_url 2026-10-28-the-backlog-what-falling-behind-costs-an-unbounded-queue %}) picks up.

Everything here reproduces from the [companion repo](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp) with `scripts/run_part3.sh`.

## A Correction to Part 2

In Part 2 I wrote that the linked queues hand around pointers. They don't. Here is `TwoLockQueue`, the Michael-Scott design from [Part 1]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), unchanged:

```cpp
bool push(const T& v) {
    Node<T>* n = new Node<T>(v);          // copy 1: into the node, no lock held
    std::lock_guard l(_t);
    tail->next.store(n, std::memory_order_release);
    tail = n;
    return true;
}

bool pop(T& out) {
    Node<T>* old;
    {
        std::lock_guard l(_h);
        auto n = head->next.load(std::memory_order_acquire);
        if (n == NULL) return false;
        out = std::move(n->value);        // copy 2: out of the node, head lock held
        old = head;
        head = n;
    }
    delete old;
    return true;
}
```

The value starts on the producer's stack and ends on the consumer's. Neither thread can point into the other's stack, so the payload has to pass through storage the queue owns: copied in because `push` takes `const T&` and the caller's object is about to go away, moved out because the node is about to be freed. For `std::array<char, N>`, which is trivially copyable, the move is also an N-byte copy. The ring does exactly the same thing with `c->value = v` and `out = std::move(c->value)`.

So every variant pays the same two N-byte transfers. What differs is what each one is holding while it pays:

| Variant | Copy in (push) | Copy out (pop) |
|---|---|---|
| `MutexQueue` (deque + 1 lock) | inside the lock | inside the lock |
| `MutexLinkedQueue` (list + 1 lock) | before locking | inside the lock, so producers wait too |
| `TwoLockQueue` | before locking | inside the head lock, so other consumers wait |
| `TwoLockQueueDeferred` (new) | before locking | after unlocking |
| `LockFreeRing<1024>` | while holding a claimed cell | while holding a claimed cell |

`TwoLockQueueDeferred` is the experiment. It is `TwoLockQueue` with the move pushed out past the unlock, plus what that costs: each node carries an extra 8-byte reference count, and every pop does two atomic decrements. Moving the copy out also creates a lifetime problem: once the head lock is released, the next consumer can unlink and free the node we are still reading. The fix gives each node two references, one for "its value has been read" and one for "it has been unlinked as the old dummy," and whichever thread drops the last one deletes it:

```cpp
bool pop(T& out) {
    NodeD* n;
    NodeD* old;
    {
        std::lock_guard l(_h);
        n = head->next.load(std::memory_order_acquire);
        if (n == nullptr) return false;
        old = head;
        head = n;
    }
    out = std::move(n->value);   // outside the lock: the point of this variant
    release(n);                  // n's value has been read
    release(old);                // old has been unlinked
    return true;
}

static void release(NodeD* x) {  // each node starts with refs = 2
    if (x->refs.fetch_sub(1, std::memory_order_acq_rel) == 1) delete x;
}
```

No thread waits for another; the `acq_rel` decrement orders the reader's move before the delete, whichever thread ends up doing it. It passes the full test suite under ThreadSanitizer and AddressSanitizer, and I checked that both sanitizers actually fire if you break the ordering on purpose.

## The Setup

Payloads are `std::array<char, N>` for N from 8 to 4096, plus `std::string` of the same lengths as a control. Each producer stamps a sequence number into the first eight bytes so the existing count-and-checksum invariant still works. Consumers pass the popped object's address through an empty `asm volatile` with a memory clobber, which forces the compiler to keep the full copy without making the consumer read N bytes itself. I checked the result in the disassembly: every consumer loop at 4 KB still contains the full `rep movsq`, and for the deferred variant it sits after the unlock, where it should.

Queues now live on the heap (a 4 KB-payload ring is 4 MB), and everything else matches Parts 1 and 2: same nine thread mixes, 1M items per producer, median of five runs, and, at first, all five variants back to back in one process. Keeping that last part was a mistake.

## The First Sweep

At 4 KB and four consumers, the first sweep had `TwoLockQueue` at 6,507 ns and the deferred variant at 1,273, a 5.1x gap; at sixteen consumers it was 7.2x.

Then I ran the deferred variant on its own, in a shorter run, and got 6,874 ns.

On its own, the variant that had just won by 5x was as slow as the one it beat. With glibc's default settings, freed memory at the top of a heap is handed back to the kernel once it passes a threshold that starts at 128 KB and [adjusts itself to the program](https://sourceware.org/glibc/manual/2.28/html_node/Memory-Allocation-Tunables.html), and the next allocation faults it back in. At 4 KB per node that costs about one page fault per item. Whether a run settles into that pattern is not a property of the variant: it shifted with whatever had run earlier in the same process, and, as it turned out, it varies from one process to the next even when a variant runs alone.

So I reran every cell from 1 KB up twice, each variant in its own process: once with default glibc, once with trimming disabled (`glibc.malloc.trim_threshold` set to 16 GB, plus a 1 GB `top_pad`).

| Xeon, 4 KB | First sweep | Own process, default | Own process, trimming off |
|---|---:|---:|---:|
| TwoLockQueue, 4P x 4C | 6507 | 6655 | 2059 |
| TwoLockQueueDeferred, 4P x 4C | 1273 | 1150 | 1333 |
| TwoLockQueue, string, 4P x 4C | 5535 | 5513 | 1444 |
| TwoLockQueueDeferred, string, 4P x 4C | 1328 | 1239 | 1295 |
| MutexLinkedQueue, 15P x 1C | 10181 | 10480 | 1930 |
| TwoLockQueue, 15P x 1C | 10260 | 1447 | 1015 |
| TwoLockQueueDeferred, 15P x 1C | 10257 | 9950 | 1449 |

The string rows are the giveaway. A pointer swap inside a lock cannot cost 4x, and with trimming off it does not: 1,444 against 1,295. Most of the 5x was the allocator. Default glibc on this machine is bimodal at large payloads. Each process lands in a fast mode around 1 to 2 microseconds or a slow one around 5 to 10, so the same variant can win in one process and lose by 7x in another, as `TwoLockQueue` at 15P x 1C does above. The page-fault counters agree: 1.04 to 1.08 faults per item for the linked queues in the slow mode at 4 KB, and under 0.001 for the ring.

The last column holds the allocator still, which is why the next section uses it. The middle column is what you would actually see in production on glibc 2.28: either mode, decided per process. I did not try jemalloc, tcmalloc or mimalloc; they make different trimming decisions, so the allocator's share may look very different under them.

## Where the Copy Happens

Four consumers, four producers, on the Xeon, with the allocator held still: payloads up to 512 bytes come from the main sweep, and 1 KB and up from the own-process runs with trimming off.

| N (bytes) | MutexQueue | TwoLockQueue | TwoLockQueueDeferred | Ring | TwoLock / Deferred |
|---:|---:|---:|---:|---:|---:|
| 8 | 530 | 1200 | 1166 | 344 | 1.03x |
| 64 | 1053 | 1218 | 1199 | 478 | 1.02x |
| 256 | 2040 | 1221 | 1160 | 476 | 1.05x |
| 512 | 2896 | 1317 | 1111 | 443 | 1.19x |
| 1024 | 3181 | 1311 | 1172 | 389 | 1.12x |
| 2048 | 3599 | 1572 | 1229 | 434 | 1.28x |
| 4096 | 4698 | 2059 | 1333 | 488 | 1.54x |

Nanoseconds per item, Xeon, 4P x 4C.

The deferred variant stays flat at 1.1 to 1.3 microseconds while `TwoLockQueue` climbs, and the gap opens with N, reaching 1.54x at 4 KB. At sixteen consumers it is 1.41x at 4 KB. The dip at 1 KB is the allocator, not the copy: `TwoLockQueue`'s node is exactly 1,032 bytes, the largest request glibc's per-thread cache (tcache) will hold, and the deferred node's extra reference count makes it 1,040. With tcache disabled for both, the 1 KB gap is 1.28x at four and at eight consumers, in line with its neighbors. `MutexQueue`, which does both copies inside its one lock, gets worse from the very first size step: going from 8 to 64 bytes roughly doubles its cost, because every extra byte lengthens the only critical section in the queue.

Two controls say this is about the copy and not about the deferred variant being generally quicker. With one consumer there is nobody else waiting on the head lock, and the two variants come out even through 2 KB (1.04x at 1 KB, 0.97x at 2 KB). At 4 KB the deferred variant is actually slower with one consumer, 1,449 ns against 1,015. I profiled it: both spend over half their cycles in the kernel's futex path, with the fifteen producers fighting over the tail lock, and the deferred variant takes about 20 percent more context switches. I haven't found the cause.

The second control is `std::string`. Its move is a pointer swap of about 32 bytes regardless of length, so moving it out of the lock should buy nothing, and from 1 KB up, where every variant ran in its own process, it buys between 0.96x and 1.11x. The string also lets you move the push-side cost instead. With a `push(T&&)` overload the producer hands its buffer to the queue instead of the queue allocating and copying a new one, and the effect depends entirely on where that copy used to sit:

| 4 KB string | `push(const T&)` | `push(T&&)` |
|---|---:|---:|
| MutexQueue, 4P x 4C | 2964 | 2285 |
| MutexQueue, 15P x 1C | 3623 | 1632 |
| TwoLockQueue, 4P x 4C | 1444 | 1362 |
| TwoLockQueue, 15P x 1C | 885 | 926 |

`MutexQueue` copies on push inside its lock, so taking the copy away helps by up to 2.2x. `TwoLockQueue` already copies before it takes the lock, so the same change is noise.

All of this is one machine, with its allocator pinned in place before the copy would show at all. The ARM instances run a newer glibc that mostly stops flipping between modes, and there the same 4 KB copy inside the head lock decides something larger than a ratio: whether the consumers keep up at all. That is [The Backlog: What Falling Behind Costs an Unbounded Queue]({% post_url 2026-10-28-the-backlog-what-falling-behind-costs-an-unbounded-queue %}).

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes, 187 GB, glibc 2.28 from the machine's stock OS, the same box as Parts 1 and 2).
Compilers: GCC 13.4.0. Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, no `-march`, linked with `-static-libstdc++` so `std::string` runs the compiler's own runtime. No code-alignment flags, to match Parts 1 and 2. An `-O3` spot check (4P x 4C and 15P x 1C at 8, 512 and 4096 bytes) agreed with `-O2` within run-to-run spread.
Methodology: 1M items per producer, 5 runs after warmup, median reported, threads pinned to consecutive core ids (which interleaves the Xeon's two NUMA nodes), synchronized start, count and checksum invariant. Payloads up to 512 bytes come from a sweep with all variants in one process; from 1 KB up, every variant ran in its own process, with default glibc and with `GLIBC_TUNABLES=glibc.malloc.trim_threshold=17179869184:glibc.malloc.top_pad=1073741824`. An `int64_t` baseline through the new harness matched Part 2's Xeon table within about 10 percent except the 1P x 1C cells, which are noisy on this machine. Re-running the 1 KB trimming-off cells two weeks later moved the 8P x 8C ratio from 0.97x to 1.11x, so treat single-cell differences of that size as noise. The single-consumer slowdown of `TwoLockQueueDeferred` on the Xeon is measured but unexplained.

**Previously:** [The Ring That Doesn't Allocate: Benchmarking a Bounded MPMC Queue]({% post_url 2026-08-21-the-ring-that-doesnt-allocate-benchmarking-a-bounded-mpmc-queue %})

**Next:** [The Backlog: What Falling Behind Costs an Unbounded Queue]({% post_url 2026-10-28-the-backlog-what-falling-behind-costs-an-unbounded-queue %})
