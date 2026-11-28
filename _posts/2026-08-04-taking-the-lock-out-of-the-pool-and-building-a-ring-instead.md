---
title: "Taking the Lock Out of the Pool, and Building a Ring Instead"
date: 2026-08-04
categories: [C++, Performance]
tags: [concurrency, queue, lock-free, treiber-stack, ring-buffer, cas, atomics, benchmarks, arm]
description: >-
  A node pool with its mutex swapped for a correct lock-free free list beats the mutex
  version in all nine producer/consumer mixes on a single-node ARM machine and loses seven
  of nine on a two-socket Xeon. On ARM, plain new/delete still beats every pool design.
  Then the design of Vyukov's bounded MPMC ring, which skips the allocator altogether.
image:
  path: /assets/img/og/taking-the-lock-out-of-the-pool-and-building-a-ring-instead.png
  alt: "Taking the Lock Out of the Pool, and Building a Ring Instead"
  hero: false
---

Take the mutex out of a node pool and the same code loses seven of nine thread mixes to the mutex version on a two-socket Xeon, then beats it in all nine on a 16-core ARM machine. The pool belongs to the two-lock queue from [A Deep Dive into Producer-Consumer Queues in C++]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), where it recycles nodes instead of calling `new` for every item. Behind one mutex it lost to plain `new`/`delete`; split across sixteen mutexes, it still lost.

Telling whether the lock or the pool was to blame took a free list with no lock at all: a Treiber stack, made correct after a [detour through the ABA problem]({% post_url 2026-07-16-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}) by packing a version counter into the top sixteen bits of each pointer. Nothing else about the queue changed.

## Removing the Mutex Made It Slower, On One Machine

With a correct lock-free pool in hand, the original question finally gets an answer. All figures are nanoseconds per item on the Xeon, median of five runs after a warmup.

| Mix | `new`/`delete` | Mutex pool | Sharded pool | Lock-free pool |
|-----------|------:|------:|------:|------:|
| 1P x 1C   | 993  | 738  | 862  | 1158 |
| 2P x 2C   | 743  | 964  | 931  | 1002 |
| 4P x 4C   | 1104 | 1174 | 1209 | 1276 |
| 8P x 8C   | 1227 | 1179 | 1364 | 1428 |
| 16P x 16C | 1203 | 1190 | 1350 | 1417 |
| 1P x 8C   | 886  | 1240 | 939  | 864  |
| 1P x 15C  | 810  | 1271 | 969  | 819  |
| 8P x 1C   | 1009 | 1027 | 1333 | 1370 |
| 15P x 1C  | 994  | 1093 | 1350 | 1360 |

Read the last two columns against each other and the shape is clean. The lock-free pool beats the mutex pool under fan-out by 30 to 36 percent, and loses to it everywhere else, by 4 to 57 percent when balanced and by 24 to 33 percent under fan-in.

Fan-out is exactly the case where one producer owns the pop side, so its compare-exchange never contends and it pays a single uncontended atomic instead of a mutex acquire and release. That is lock-free working as advertised. Everything else is the other half of the bargain. When fifteen threads hammer one word, a compare-exchange loop has no way to stand down: each failed attempt reloads the cache line, computes a new candidate, and fires again, and every one of those retries is coherence traffic that slows down the thread that is actually going to win. A contended mutex does the opposite. It parks the loser in the kernel and stops it generating traffic at all.

*Update, November 2026: after Fedor Pikus's CppCon talk I'd put this differently. His spinlock with backoff never sleeps and still beats a CAS loop under contention, so sleeping is just one way of standing down. More in the [CppCon trip report]({% post_url 2026-09-29-a-week-in-aurora-cppcon-2026 %}).*

Then I ran the same code on the ARM box, and the column flipped:

| Mix (ARM) | `new`/`delete` | Mutex pool | Sharded pool | Lock-free pool |
|-----------|------:|------:|------:|------:|
| 1P x 1C   | 340 | 648  | 362 | 464 |
| 2P x 2C   | 459 | 706  | 497 | 541 |
| 4P x 4C   | 520 | 888  | 538 | 649 |
| 8P x 8C   | 752 | 963  | 793 | 857 |
| 16P x 16C | 844 | 1164 | 976 | 957 |
| 1P x 8C   | 588 | 779  | 647 | 609 |
| 1P x 15C  | 620 | 779  | 641 | 678 |
| 8P x 1C   | 394 | 632  | 479 | 615 |
| 15P x 1C  | 448 | 715  | 548 | 628 |

On the Xeon the lock-free pool beat the single-mutex pool in two mixes out of nine. On ARM it beats it in all nine, by 3 to 28 percent. Nothing about the code changed. The same compare-exchange loop that was a liability on a two-socket Skylake is an asset on a single-NUMA-node Neoverse-N1, and this is the same divergence [Part 1]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}) kept running into: the Xeon interleaves its two NUMA nodes by core id, so every contended line in a multi-thread run crosses the socket interconnect to move. Retry loops pay that toll once per failed attempt. On a machine where the line never leaves the node, retrying is cheap enough that never entering the kernel wins.

Beating one mutex is a low bar, though, and I already had sixteen. Put the lock-free pool against the sharded pool from Part 1 and it loses seven of nine mixes on ARM, winning only 16P x 16C and 1P x 8C. Sixteen aligned mutexes spread the traffic well enough that going lock-free on top of that buys nothing on this machine. (The 16P x 16C row on ARM is 32 threads on 16 cores, so every variant there is paying scheduler overhead, not just cache traffic.)

The comparison that matters more is the first column against the other three, and there the two machines mostly agree. Three pool designs (one mutex, sixteen sharded mutexes, and no mutex at all), and plain `new`/`delete` beats every one of them in all nine mixes on ARM, and in five of nine on the Xeon. Part 1's diagnosis was that glibc's [per-thread arenas](https://sourceware.org/glibc/wiki/MallocInternals) already do the sharding a hand-rolled pool is trying to reinvent. Removing the lock does not change that, because the lock was never the whole problem: the pool is a single shared structure that every thread on both ends of the queue must touch, and the allocator is not.

## Not Allocating At All

If the allocation is the problem, stop allocating. A bounded ring buffer holds its storage in a fixed array built once in the constructor, so the steady state touches no allocator on any path. This is [Dmitry Vyukov's bounded MPMC queue](https://www.1024cores.net/home/lock-free-algorithms/queues/bounded-mpmc-queue), and the interesting part is how it avoids the gap that sinks a naive ring.

The naive version keeps a head and a tail counter and has a hole: a producer claims a slot, then stalls before writing the value, and a consumer that trusts the tail counter reads whatever garbage was in the slot. Vyukov's fix is to give every cell a sequence number, so the cell itself is the synchronization point:

```
producer holding ticket pos:
  seq == pos       -> cell is free: claim the ticket and write it
  seq <  pos       -> last lap's value is still unread: FULL
consumer holding ticket pos:
  seq == pos + 1   -> cell is full: claim the ticket and read it
  seq <  pos + 1   -> nothing published here yet: EMPTY
```

```cpp
bool push(const T& v) {
    Cell* c;
    size_t pos = _enq.load(std::memory_order_relaxed);
    for (;;) {
        c = &_buf[pos & MASK];
        size_t seq = c->seq.load(std::memory_order_acquire);
        intptr_t diff = static_cast<intptr_t>(seq) - static_cast<intptr_t>(pos);
        if (diff == 0) {
            if (_enq.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed))
                break;
        } else if (diff < 0) {
            return false;                                  // full
        } else {
            pos = _enq.load(std::memory_order_relaxed);    // stale ticket, re-read
        }
    }
    c->value = v;
    c->seq.store(pos + 1, std::memory_order_release);
    return true;
}
```

The shared counter only hands out tickets; correctness lives in the cells. A consumer waiting on `seq == pos + 1` cannot be fooled, because only the producer that owns that ticket can publish it.

The signed `diff` is doing quiet work. Both `pos` and `seq` are unsigned counters that will eventually wrap around zero, but their difference stays small and correct across the wrap, so the four `seq` cases keep working forever.

`pop` is the mirror image, with one extra step at the end:

```cpp
bool pop(T& out) {
    Cell* c;
    size_t pos = _deq.load(std::memory_order_relaxed);
    for (;;) {
        c = &_buf[pos & MASK];
        size_t seq = c->seq.load(std::memory_order_acquire);
        intptr_t diff = static_cast<intptr_t>(seq) - static_cast<intptr_t>(pos + 1);
        if (diff == 0) {
            if (_deq.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed))
                break;
        } else if (diff < 0) {
            return false;                                  // empty
        } else {
            pos = _deq.load(std::memory_order_relaxed);
        }
    }
    out = std::move(c->value);
    // Hand the cell to the producer one full lap ahead.
    c->seq.store(pos + Capacity, std::memory_order_release);
    return true;
}
```

That last store is what makes it a ring. The consumer that empties a cell sets its sequence number to `pos + Capacity`, which is exactly the ticket the producer one lap ahead will arrive holding.

A stalled producer is where the cell-level design shows its limit. The garbage read is gone: a consumer that reaches a claimed but unwritten cell finds `seq == pos`, reports EMPTY, and tries again later. But consumers take tickets in order, so every one of them stops at that cell, even when the cells behind it are full:

![An eight-cell ring. The dequeue ticket is 10 and points at cell 2, which a stalled producer claimed but never wrote, so it still holds seq 10. Cells 3 to 5 are full with seq 12, 13 and 14. The consumer at ticket 10 sees seq 10 instead of 11 and returns empty.](/assets/img/vyukov-ring-stall-light.svg){: .light w="720" h="236" }
![An eight-cell ring. The dequeue ticket is 10 and points at cell 2, which a stalled producer claimed but never wrote, so it still holds seq 10. Cells 3 to 5 are full with seq 12, 13 and 14. The consumer at ticket 10 sees seq 10 instead of 11 and returns empty.](/assets/img/vyukov-ring-stall-dark.svg){: .dark w="720" h="236" }
_Capacity 8. The producer holding ticket 10 claimed cell 2 and was preempted before its release store._

Other producers keep filling the cells after it until the ring laps around to the stuck one, and then they stop too. One preempted thread can stall the whole queue, which makes the ring blocking in the formal sense, for all that it never takes a lock.

How fast that is in practice, and where it still loses, is in [The Ring That Doesn't Allocate: Benchmarking a Bounded MPMC Queue]({% post_url 2026-08-25-the-ring-that-doesnt-allocate-benchmarking-a-bounded-mpmc-queue %}).

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp); the tables reproduce with `make bench-queue`.

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes); ARM Neoverse-N1 (16 cores, single NUMA node). Note that 16P x 16C is 32 threads on both, which fits the Xeon's 32 cores and oversubscribes ARM's 16.
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, identical on both architectures (no `-march`) for a clean cross-architecture comparison.
Methodology: 1M items per producer, 5 runs after warmup, median reported, each thread pinned to its own core by consecutive core id (which interleaves the Xeon's two NUMA nodes, so multi-thread runs span both sockets), synchronized start, post-run count and checksum invariant.

**Previously:** [Lock-Free Is Not Free: ABA and Tagged Pointers]({% post_url 2026-07-16-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %})

**Next:** [The Ring That Doesn't Allocate: Benchmarking a Bounded MPMC Queue]({% post_url 2026-08-25-the-ring-that-doesnt-allocate-benchmarking-a-bounded-mpmc-queue %})
