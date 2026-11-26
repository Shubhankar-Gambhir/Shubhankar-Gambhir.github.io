---
title: "The Ring That Doesn't Allocate: Benchmarking a Bounded MPMC Queue"
date: 2026-08-21
categories: [C++, Performance]
tags: [concurrency, queue, lock-free, ring-buffer, cas, atomics, benchmarks, arm]
description: >-
  Vyukov's bounded MPMC ring moves an int64_t in 90 ns on a two-socket Xeon and 32 ns on
  ARM when uncontended, and stays within 1.4x across the Xeon's contended mixes. It still
  loses fan-in to a deque behind one mutex, and re-running the ARM numbers overturned one
  of Part 1's conclusions.
---

The ring in this post is [Dmitry Vyukov's bounded MPMC queue](https://www.1024cores.net/home/lock-free-algorithms/queues/bounded-mpmc-queue): a fixed array of cells allocated once in the constructor, a shared counter, `_enq`, that hands out tickets, and a sequence number in every cell that tells whoever holds a ticket whether the cell is theirs yet. In steady state it touches no allocator and takes no lock. The [previous post]({% post_url 2026-08-20-taking-the-lock-out-of-the-pool-faster-on-arm-slower-on-the-xeon %}) walks through the code.

Its competition is the rest of the queue lab. From [Part 1]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}) come a `std::deque` behind one mutex and a two-lock linked queue that calls `new`/`delete` for every node. The third is that two-lock queue drawing its nodes from a lock-free pool, the last of three attempts at making node allocation cheaper; like the other two, it mostly lost to plain `new`/`delete`. Every variant moves `int64_t` items through one shared harness across nine producer/consumer mixes, on a two-socket Xeon and a 16-core ARM machine, and the ring runs at two capacities, 1024 and 65536 slots.

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

The property I did not expect is the flatness. Across the eight contended mixes the ring spans 424 to 598 nanoseconds, a spread of 1.4x, against 353 to 3762 for the deque-backed queue, a spread of more than ten. The two-lock queue is respectably flat too at 1.65x, so the ring is not alone in this, but it is flat at roughly half the absolute cost. For a component sitting in a system whose traffic shape you do not control, that predictability is arguably worth more than any single number in the table.

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

Second, capacity looked like a rounding error until it wasn't. On the Xeon, sixty-four times more slots buys 1 to 7 percent contended and costs 16 percent uncontended, where the smaller ring's cache footprint wins. On ARM at 16P x 16C the same change is worth 2.1x, 763 nanoseconds down to 357. That mix is 32 threads on 16 cores, so a 1024-slot ring spends its life alternately full and empty while descheduled threads hold tickets, and every spin on a false return is wasted. The bigger ring absorbs the jitter. If your thread count can exceed your core count, size the ring for the stall, not for the steady state. The cells are also deliberately unpadded, matching the canonical layout, so four `int64_t` cells share a cache line and producers holding consecutive tickets do contend. That is [false sharing]({% post_url 2026-06-24-what-does-a-lock-actually-cost-concurrent-counter-benchmarks %}) by choice rather than by accident, and padding it away trades four times the footprint for it, which is a bet on your capacity and thread count that I did not run.

Third, and this is the one I would raise if I were reading someone else's post: none of this is measured against a production queue. `boost::lockfree::queue`, `moodycamel::ConcurrentQueue`, and `folly::MPMCQueue` all exist, all have had considerably more attention paid to them than a weekend's worth, and any of them is a better default than code you wrote yourself after reading a blog post. This lab is built to explain where the time goes across a controlled sequence of designs, and every variant in it shares one harness so the comparisons between them are fair. It is not a bake-off, and I would not read the ring's numbers as a claim that it beats a library I never ran.

## A Correction to Part 1

Re-running the Part 1 variants to get these ARM numbers cost that post one of its conclusions. Part 1 argued that the two-lock queue's fan-in penalty was a property of the two-socket Xeon, citing an ARM run where the two-lock queue beat the single-lock linked list at 15P x 1C, 321 nanoseconds against 378. On this ARM machine the ordering is reversed: 345 for the single-lock linked queue against 448 for the two-lock. Same CPU model, same core count, same GCC 13.3.0, same flags, different rented instance, opposite result.

I do not know which run is representative. My best reading is that this particular comparison sits inside the noise floor of a setup where I control neither placement nor neighbors, which makes Part 1's "solid and repeatable" too strong for that one claim. What I would still defend are the results with large margins and a mechanism behind them: fan-out favoring the split lock by 3.7x or more, the deque winning uncontended, every pool variant losing to the allocator on ARM. A 15 percent gap between two linked variants on a cloud instance is not one of those.

## Where That Leaves the Pool

Three attempts to make allocation cheaper, and the thing that won on both machines was the ring, which doesn't allocate.

There is one more thread to pull. Every queue here moves an `int64_t`, which is the friendliest possible payload: it fits in a register, copies for free, and makes the synchronization the entire cost. That is what I wanted for measuring locks, but it flatters the linked variants, which hand around pointers to nodes, and penalizes the ring, which copies values into cells. A payload that costs real money to copy should move the ranking, possibly a lot. That is the next thing to measure. (It did move the ranking, though not the way I predicted here: see [The Copy: What a Bigger Payload Does to a Concurrent Queue]({% post_url 2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue %}).)

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes); ARM Neoverse-N1 (16 cores, single NUMA node). Note that 16P x 16C is 32 threads on both, which fits the Xeon's 32 cores and oversubscribes ARM's 16.
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, identical on both architectures (no `-march`) for a clean cross-architecture comparison.
Methodology: 1M items per producer, 5 runs after warmup, median reported, each thread pinned to its own core by consecutive core id (which interleaves the Xeon's two NUMA nodes, so multi-thread runs span both sockets), synchronized start, post-run count and checksum invariant.

**Previously:** [Taking the Lock Out of the Pool: Faster on ARM, Slower on the Xeon]({% post_url 2026-08-20-taking-the-lock-out-of-the-pool-faster-on-arm-slower-on-the-xeon %})

**Next:** [The Copy: What a Bigger Payload Does to a Concurrent Queue]({% post_url 2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue %})
