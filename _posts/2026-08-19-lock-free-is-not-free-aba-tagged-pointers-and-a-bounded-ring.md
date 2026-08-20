---
title: "Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring"
date: 2026-08-19
categories: [C++, Performance]
tags: [concurrency, queue, lock-free, aba, treiber-stack, tagged-pointer, ring-buffer, cas, atomics, benchmarks, arm]
description: >-
  I replaced a mutex with a textbook Treiber stack and it silently lost 83 percent of the
  items pushed through it. Here is the ABA bug, a reproducer that measures which thread
  mixes trigger it on three machines, the tagged-pointer fix, and why deleting the mutex
  helped on ARM and hurt on x86. Then a bounded ring that beats all of it.
image:
  path: /assets/img/og/lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring.png
  alt: "Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring"
  hero: false
---

The [previous post]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}) ended with one suspect still standing. A node pool guarded by a mutex lost to plain `new`/`delete`. Sharding that pool sixteen ways recovered some of the loss and still lost. The open question was whether the lock was the only problem, or whether pooling is simply a dead end for this workload. The way to settle it is to take the lock away.

So I did, twice. First by replacing the free list's mutex with a lock-free Treiber stack, which is the move everyone reaches for. Then by deleting the allocation question entirely with a bounded ring buffer that touches no allocator at all in steady state.

The first attempt lost 83 percent of the items pushed through it, and my test suite told me it was fine. That is most of this post.

The companion repo is [on GitHub](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp). Everything here reproduces with `make bench-queue` and `make aba-demo`.

## The Obvious Move

The free list is a stack of spare nodes. Producers pop one in `push`, consumers return one in `pop`. Making a stack lock-free is the canonical introductory exercise: a Treiber stack, two compare-exchange retry loops, about fifteen lines.

```cpp
Node<T>* getNode() {
    Node<T>* old = _freeList.load(std::memory_order_acquire);
    for (;;) {
        if (old == NULL) return new Node<T>();
        Node<T>* next = old->next.load(std::memory_order_relaxed);
        if (_freeList.compare_exchange_weak(old, next,
                                            std::memory_order_acq_rel,
                                            std::memory_order_acquire)) {
            old->next.store(NULL, std::memory_order_relaxed);
            return old;
        }
    }
}

void freeNode(Node<T>* n) {
    Node<T>* head = _freeList.load(std::memory_order_relaxed);
    do {
        n->next.store(head, std::memory_order_relaxed);
    } while (!_freeList.compare_exchange_weak(head, n,
                                              std::memory_order_acq_rel,
                                              std::memory_order_relaxed));
}
```

`compare_exchange_weak` rather than `_strong` is deliberate. The weak form is allowed to fail spuriously, which costs nothing inside a retry loop that was going to loop anyway, and on architectures that implement compare-exchange as a load-linked/store-conditional pair it avoids an inner retry the strong form would have to do itself. On x86-64 both lower to `cmpxchg` and the choice is free.

The queue around it is unchanged from the previous post: separate head and tail mutexes, an atomic `next` link with release on publish and acquire on read. Only the free list changed.

I built it, ran `make test-queue`, and got twenty-seven passes out of twenty-seven. Single-threaded FIFO, balanced four-by-four, fan-out, fan-in, count and checksum invariants, all green.

Then I ran the same binary forty more times.

## Forty Out of Forty

Every one of those forty runs failed, all in the same place:

```
[TwoLockLFPool] 8P x 1C x 100000 -> count=516864/800000 sum=16960450423/40000400000  FAIL
[TwoLockLFPool] 8P x 1C x 100000 -> count=8815/800000   sum=4922931/40000400000      FAIL
[TwoLockLFPool] 8P x 1C x 100000 -> count=320235/800000 sum=6510990442/40000400000   FAIL
```

Not a subtle off-by-one. In the second line the queue delivered 8,815 of 800,000 items and lost the rest. The first run had passed by luck, and one green run was all it took for me to believe the thing worked.

This is the ABA problem, and the reason it is worth measuring rather than reciting is that the textbook description makes it sound like a rare interleaving. Here is the sequence. Thread A reads the head of the free list, sees node N1, and reads `N1->next`, which is N2. Before A's compare-exchange runs, thread B pops N1 off the stack and starts using it. Some consumer later returns N1 to the free list. The head is N1 again, but the stack underneath it is now completely different. A's compare-exchange compares the head against N1, finds a match, and swings the head to N2, a node that is currently live inside the queue. N2 now exists in two places at once, and from there the structure unravels.

A bare pointer compare-exchange cannot distinguish "nothing changed" from "everything changed and changed back."

So rather than argue about how likely that is, I turned it into a measurement. `make aba-demo` runs the broken queue twenty times per thread mix and reports how often the count-and-checksum invariant broke and by how much:

| Mix | Xeon corrupted | Xeon mean deviation | ARM corrupted | ARM mean deviation |
|-----------|------:|---------:|------:|---------:|
| 1P x 1C   | 0/20  | 0 | 0/20  | 0 |
| 4P x 4C   | 0/20  | 0 | 0/20  | 0 |
| 8P x 8C   | 0/20  | 0 | 1/20  | 48 |
| 1P x 8C   | 0/20  | 0 | 0/20  | 0 |
| 1P x 15C  | 0/20  | 0 | 0/20  | 0 |
| 8P x 1C   | 4/20  | 220,139 | 11/20 | 131,662 |
| 15P x 1C  | 9/20  | 677,903 | 7/20  | 576,915 |

Deviation rather than loss, because ABA both drops nodes and hands the same node to two threads, so a corrupted run can deliver more items than went in. The 48-item deviation on ARM deserves a stare: the same defect that vaporized 744,783 items in one trial quietly miscounted by 48 in another. A check with any tolerance at all would have called that one green.

The zeros are as informative as the failures. Every single-producer mix is clean at any consumer count, and that is structural rather than lucky: `getNode` is only ever called from `push`, so with one producer the stack's pop side is never concurrent, and ABA on a stack pop needs two poppers racing. You cannot trigger this bug by adding consumers. Fan-out is immune by construction.

What does trigger it is fan-in, and the reason is the depth of the free list. With one consumer returning nodes and fifteen producers taking them, the pool is starved down to a node or two in constant circulation. The window ABA needs, meaning a node leaving the top of the stack and coming back before another thread finishes its compare-exchange, collapses from "a full traversal of the queue" to "one handoff." Fan-in does not just expose this race, it manufactures it.

## The Same Bug, Different Odds

Line up the three machines I ran this on and the numbers refuse to agree. At 8P x 1C the Xeon corrupts 4 trials in 20 and ARM corrupts 11, while a 64-core AMD EPYC (Rome) virtual machine corrupts every one. That EPYC box is where I first hit this, and where the same binary failed forty runs out of forty. The balanced 8P x 8C mix is spotless on the Xeon across 20 trials, breaks once on ARM, and breaks 3 times in 20 on the EPYC. Totalled across all seven mixes: 13 of 140, 19 of 140, 46 of 140.

One source file, one bug, and the odds of catching it swing by a factor of three with nothing but the hardware. "Our test suite is green on CI" would have been a statement about the CI machine's core count and memory system rather than about the code. That is the failure mode to internalize: a stress test samples a probability distribution that moves when you change machines, and it samples it a handful of times. The Xeon's clean 8P x 8C row is not evidence that balanced workloads are safe, only evidence that this Xeon did not happen to hit the window in twenty tries.

The broken variant is still in the repo, marked as broken and excluded from the test suite, because a reproducer for ABA that fails on demand turns out to be hard to find and useful to have.

## Making the Pointer Change

The fix is to make the head word change value on every modification, so a returning pointer no longer compares equal to itself. The usual approach pairs the pointer with a counter and compare-exchanges both together, which on x86-64 means a 128-bit `cmpxchg16b` and on AArch64 means the LSE `CASP` instruction. Both need `-march` flags, and this lab deliberately compiles with identical flags on both architectures so the cross-architecture comparison stays clean.

There is a cheaper trick. On both x86-64 and AArch64, Linux hands out user-space addresses below 2^47 by default, so the top sixteen bits of any pointer we own are zero and available to borrow:

```
 63          48 47                                    0
 +-------------+---------------------------------------+
 |   version   |            node address               |
 +-------------+---------------------------------------+
```

That makes the whole thing a plain `std::atomic<uint64_t>`, lock-free on both architectures with no special flags:

```cpp
static uint64_t pack(Node<T>* p, uint64_t tag) {
    return (reinterpret_cast<uint64_t>(p) & PTR_MASK) | ((tag & TAG_MASK) << TAG_SHIFT);
}

Node<T>* getNode() {
    uint64_t old = _freeList.load(std::memory_order_acquire);
    for (;;) {
        Node<T>* p = ptr_of(old);
        if (p == NULL) return make_node();
        Node<T>* next = p->next.load(std::memory_order_relaxed);
        if (_freeList.compare_exchange_weak(old, pack(next, tag_of(old) + 1),
                                            std::memory_order_acq_rel,
                                            std::memory_order_acquire)) {
            p->next.store(NULL, std::memory_order_relaxed);
            return p;
        }
    }
}
```

Both `getNode` and `freeNode` bump the version, so any observer that read the old head sees a different word no matter which side raced it. Every node that can reach the free list is born in one place, `make_node`, which asserts that the address really does fit rather than trusting the layout silently.

That assert is not decoration. "Pointers are 48 bits" describes a default, not the architecture: x86-64 with [5-level paging](https://docs.kernel.org/arch/x86/x86_64/5level-paging.html) supports 57-bit addresses, and AArch64 with LVA supports 52-bit ones. Linux keeps allocations below the 47-bit boundary on both unless a program explicitly asks for more via an `mmap` hint, largely so that pointer-tagging code like this keeps working. Request a high address anywhere in the process and the packing corrupts silently, which is why the check runs on every node instead of living in a comment.

Being honest about what this buys: sixteen bits of version wraps every 65,536 modifications, so ABA is not eliminated, only made astronomically unlikely. An interfering thread would have to perform an exact multiple of 65,536 free-list operations inside another thread's compare-exchange window. A 128-bit tagged pointer removes the wrap; hazard pointers or epoch-based reclamation remove the whole class of problem by making it safe to touch a node that might have been recycled. The repo implements none of those, and if you need a guarantee rather than a probability you should reach for one of them instead of this. What I have survives thirty consecutive runs of the full test suite on both benchmark machines and forty on the EPYC box, which is enough to benchmark with and is not a proof.

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

Read the last two columns against each other and the shape is clean. The lock-free pool beats the mutex pool under fan-out by 30 to 36 percent, and loses to it everywhere else, by 9 to 20 percent when balanced and by 24 to 33 percent under fan-in.

Fan-out is exactly the case where one producer owns the pop side, so its compare-exchange never contends and it pays a single uncontended atomic instead of a mutex acquire and release. That is lock-free working as advertised. Everything else is the other half of the bargain. When fifteen threads hammer one word, a compare-exchange loop has no way to stand down: each failed attempt reloads the cache line, computes a new candidate, and fires again, and every one of those retries is coherence traffic that slows down the thread that is actually going to win. A contended mutex does the opposite. It parks the loser in the kernel and stops it generating traffic at all. Under heavy contention, going to sleep is a feature.

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

On the Xeon the lock-free pool beat the single-mutex pool in one mix out of nine. On ARM it beats it in all nine, by 3 to 27 percent. Nothing about the code changed. The same compare-exchange loop that was a liability on a two-socket Skylake is an asset on a single-NUMA-node Neoverse-N1, and this is the same divergence [Part 1]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}) kept running into: the Xeon interleaves its two NUMA nodes by core id, so every contended line in a multi-thread run crosses the socket interconnect to move. Retry loops pay that toll once per failed attempt. On a machine where the line never leaves the node, retrying is cheap enough that never entering the kernel wins.

The honest caveat is that beating one mutex is a low bar, and I already had sixteen. Put the lock-free pool against the sharded pool from Part 1 and it loses seven of nine mixes on ARM, winning only 16P x 16C and 1P x 8C. Sixteen aligned mutexes spread the traffic well enough that going lock-free on top of that buys nothing on this machine. (The 16P x 16C row on ARM is 32 threads on 16 cores, so every variant there is paying scheduler overhead, not just cache traffic.)

The comparison that matters more is the first column against the other three, and there the two machines finally agree. Three pool designs (one mutex, sixteen sharded mutexes, and no mutex at all), and plain `new`/`delete` beats every one of them in all nine mixes on ARM, and in every balanced and fan-in mix on the Xeon. Part 1's diagnosis was that glibc's [per-thread arenas](https://sourceware.org/glibc/wiki/MallocInternals) already do the sharding a hand-rolled pool is trying to reinvent. Removing the lock does not change that, because the lock was never the whole problem: the pool is a single shared structure that every thread on both ends of the queue must touch, and the allocator is not.

Pooling was a dead end on both architectures. The lock was a symptom.

## Not Allocating At All

If the allocation is the problem, stop allocating. A bounded ring buffer holds its storage in a fixed array built once in the constructor, so the steady state touches no allocator on any path. This is [Dmitry Vyukov's bounded MPMC queue](https://www.1024cores.net/home/lock-free-algorithms/queues/bounded-mpmc-queue), and the interesting part is how it avoids the gap that sinks a naive ring.

The naive version keeps a head and a tail counter and has a hole: a producer claims a slot, then stalls before writing the value, and a consumer that trusts the tail counter reads whatever garbage was in the slot. Vyukov's fix is to give every cell a sequence number, so the cell itself is the synchronization point:

```
seq == pos       -> cell is free; the producer holding ticket pos may write it
seq == pos + 1   -> cell is full; the consumer holding ticket pos may read it
seq <  pos       -> producers have lapped the consumers: FULL
seq <  pos + 1   -> consumers have caught the producers: EMPTY
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

The shared counter only hands out tickets; correctness lives in the cells. A consumer waiting on `seq == pos + 1` cannot be fooled, because only the producer that owns that ticket can publish it. And a stalled producer blocks exactly one cell rather than the queue, which is the property the head/tail version lacks.

The signed `diff` is doing quiet work. Both `pos` and `seq` are unsigned counters that will eventually wrap around zero, but their difference stays small and correct across the wrap, so the four cases above keep working forever.

## The Ring

Same harness, same nine mixes, Xeon first:

| Mix (Xeon) | Deque + 1 mutex | Two locks | Lock-free pool | Ring (1024) | Ring (65536) |
|-----------|------:|------:|------:|------:|------:|
| 1P x 1C   | 379  | 993  | 1158 | **90**  | 104 |
| 2P x 2C   | 611  | 743  | 1002 | 424 | **408** |
| 4P x 4C   | 596  | 1104 | 1276 | 472 | **453** |
| 8P x 8C   | 593  | 1227 | 1428 | 505 | **483** |
| 16P x 16C | 576  | 1203 | 1417 | 598 | **559** |
| 1P x 8C   | 2141 | 886  | 864  | 433 | **415** |
| 1P x 15C  | 3762 | 810  | 819  | 460 | **426** |
| 8P x 1C   | **353**  | 1009 | 1370 | 429 | 425 |
| 15P x 1C  | **368**  | 994  | 1360 | 450 | 430 |

Uncontended, the ring is 90 nanoseconds per item, four times faster than the deque-backed mutex queue and eleven times faster than the two-lock linked queue. Nothing else in the lab is close. There is no allocation, no lock, and the whole working set at capacity 1024 is sixteen kilobytes that stays resident in L2.

The jump from 90 to 424 nanoseconds at 2P x 2C is the real cost of multi-producer support arriving all at once: the moment two threads share `_enq`, every ticket claim is a contended compare-exchange on one cache line, and on a two-socket Xeon that line is crossing sockets.

The property I did not expect is the flatness. Across the eight contended mixes the ring spans 408 to 598 nanoseconds, a spread of 1.4x, against 353 to 3762 for the deque-backed queue, a spread of more than ten. The two-lock queue is respectably flat too at 1.65x, so the ring is not alone in this, but it is flat at roughly half the absolute cost. For a component sitting in a system whose traffic shape you do not control, that predictability is arguably worth more than any single number in the table.

ARM keeps the shape and moves the extremes:

| Mix (ARM) | Deque + 1 mutex | Two locks | Lock-free pool | Ring (1024) | Ring (65536) |
|-----------|------:|------:|------:|------:|------:|
| 1P x 1C   | 206  | 340 | 464 | 34  | **32**  |
| 2P x 2C   | 247  | 459 | 541 | **170** | 172 |
| 4P x 4C   | 418  | 520 | 649 | **213** | 217 |
| 8P x 8C   | 389  | 752 | 857 | **322** | 326 |
| 16P x 16C | 486  | 844 | 957 | 763 | **357** |
| 1P x 8C   | 1367 | 588 | 609 | **328** | 350 |
| 1P x 15C  | 2189 | 620 | 678 | 541 | **510** |
| 8P x 1C   | **248**  | 394 | 615 | 364 | 379 |
| 15P x 1C  | **245**  | 448 | 628 | 539 | 500 |

Thirty-two nanoseconds per item uncontended, six times faster than the deque and nearly three times the best number the Xeon could produce. A single-producer single-consumer ring on a single-NUMA-node machine is close to the floor for moving data between two threads: one cache line handed from a core to its neighbor, no kernel, no allocator, no atomic that anyone else wants.

Fan-in is where the ring genuinely loses, and ARM makes it obvious. The deque under one mutex takes 245 nanoseconds at 15P x 1C against the ring's 539, a gap of 2.2x where the Xeon's was only 1.2x. The deque's fan-in strength is the one result that has survived every machine in this series so far: when a lone consumer is the bottleneck, contiguous storage behind one lock is an excellent design, and no amount of lock-freedom on the producer side helps a consumer that is already saturated.

Three caveats before anyone puts this in production on my say-so. First, the ring is bounded and everything it is compared against is not, which is a different contract: `push` returns false instead of growing, and the harness spins on false, so a full ring shows up as producer spin time rather than an error. Backpressure is often what you want, but it is a decision, not a free win.

Second, capacity looked like a rounding error until it wasn't. On the Xeon, sixty-four times more slots buys 3 to 7 percent contended and costs 15 percent uncontended, where the smaller ring's cache footprint wins. On ARM at 16P x 16C the same change is worth 2.1x, 763 nanoseconds down to 357. That mix is 32 threads on 16 cores, so a 1024-slot ring spends its life alternately full and empty while descheduled threads hold tickets, and every spin on a false return is wasted. The bigger ring absorbs the jitter. If your thread count can exceed your core count, size the ring for the stall, not for the steady state. The cells are also deliberately unpadded, matching the canonical layout, so four `int64_t` cells share a cache line and producers holding consecutive tickets do contend. That is [false sharing]({% post_url 2026-06-24-what-does-a-lock-actually-cost-concurrent-counter-benchmarks %}) by choice rather than by accident, and padding it away trades four times the footprint for it, which is a bet on your capacity and thread count that I did not run.

Third, and this is the one I would raise if I were reading someone else's post: none of this is measured against a production queue. `boost::lockfree::queue`, `moodycamel::ConcurrentQueue`, and `folly::MPMCQueue` all exist, all have had considerably more attention paid to them than a weekend's worth, and any of them is a better default than code you wrote yourself after reading a blog post. This lab is built to explain where the time goes across a controlled sequence of designs, and every variant in it shares one harness so the comparisons between them are fair. It is not a bake-off, and I would not read the ring's numbers as a claim that it beats a library I never ran.

## A Correction to Part 1

Re-running the Part 1 variants to get these ARM numbers cost that post one of its conclusions. Part 1 argued that the two-lock queue's fan-in penalty was a property of the two-socket Xeon, citing an ARM run where the two-lock queue beat the single-lock linked list at 15P x 1C, 321 nanoseconds against 378. On this ARM machine the ordering is reversed: 345 for the single-lock linked queue against 448 for the two-lock. Same CPU model, same core count, same GCC 13.3.0, same flags, different rented instance, opposite result.

I do not know which run is representative, and I am not going to guess. The honest reading is that this particular comparison sits inside the noise floor of a setup where I control neither placement nor neighbors, which makes Part 1's "solid and repeatable" too strong for that one claim. What I would still defend are the results with large margins and a mechanism behind them: fan-out favoring the split lock by 3.7x or more, the deque winning uncontended, every pool variant losing to the allocator. A 15 percent gap between two linked variants on a cloud instance is not one of those.

## What I Learned

Removing a mutex is not an optimization. It is a change of failure mode. The Treiber stack was shorter than the mutex version, contained no lock, matched every reference implementation I could find, and silently destroyed 83 percent of the data flowing through it. The test suite passed on the first run. What eventually caught it was running the same binary forty more times, and what explained it was a program that measures the failure rate per thread mix instead of asserting once and hoping.

The performance lesson is smaller and less romantic than the correctness one. A compare-exchange loop cannot stand down and a mutex can, so the retry loop's value depends entirely on what a failed retry costs on your machine. On the two-socket Xeon, where every failure drags a cache line back across the interconnect, the lock-free pool won one mix out of nine. On the single-node ARM box, where it does not, the same code won all nine. Neither number is the truth about lock-free pools. Both are facts about a memory system, and I would not have known that from one machine.

What actually won was not lock-freedom on either box. It was deleting the work. Every pool variant, locked or not, lost to plain `new`/`delete`; the ring wins because it does not allocate at all. Three attempts to make allocation cheaper, and the answer was to arrange not to allocate.

There is one more thread to pull. Every queue here moves an `int64_t`, which is the friendliest possible payload: it fits in a register, copies for free, and makes the synchronization the entire cost. That is what I wanted for measuring locks, but it flatters the linked variants, which hand around pointers to nodes, and penalizes the ring, which copies values into cells. A payload that costs real money to copy should move the ranking, possibly a lot. That is the next thing to measure.

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes); ARM Neoverse-N1 (16 cores, single NUMA node). Note that 16P x 16C is 32 threads on both, which fits the Xeon's 32 cores and oversubscribes ARM's 16.
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, identical on both architectures (no `-march`) for a clean cross-architecture comparison.
Methodology: 1M items per producer, 5 runs after warmup, median reported, each thread pinned to its own core by consecutive core id (which interleaves the Xeon's two NUMA nodes, so multi-thread runs span both sockets), synchronized start, post-run count and checksum invariant.
ABA rates: 20 trials per mix at 100k items per producer, reported as absolute deviation from the expected item count. The third machine cited is a 64-core AMD EPYC (Rome) VM running GCC 11.4.0, a different compiler from the other two, so treat its higher corruption rate as one more data point rather than a controlled comparison.

Previous: [A Deep Dive into Producer-Consumer Queues in C++]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %})
