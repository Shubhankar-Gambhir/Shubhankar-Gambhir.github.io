---
title: "Lock-Free Is Not Free: ABA and Tagged Pointers"
date: 2026-07-16
categories: [C++, Performance]
tags: [concurrency, queue, lock-free, aba, treiber-stack, tagged-pointer, cas, atomics, benchmarks, arm]
description: >-
  I replaced a mutex with a textbook Treiber stack and it silently lost 83 percent of the
  items pushed through it. Here is the ABA bug, a reproducer that measures which thread
  mixes trigger it on three machines, and the tagged-pointer fix that borrows the top
  sixteen bits of every pointer for a version counter.
image:
  path: /assets/img/og/lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring.png
  alt: "Lock-Free Is Not Free: ABA and Tagged Pointers"
  hero: false
---

The [previous post]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}) ended with one suspect still standing. A node pool guarded by a mutex lost to plain `new`/`delete`. Sharding that pool sixteen ways recovered some of the loss and still lost. The open question was whether the lock was the only problem, or whether pooling is simply a dead end for this workload. The way to settle it is to take the lock away.

So I did, twice. First by replacing the free list's mutex with a lock-free Treiber stack, which is the move everyone reaches for. Then by deleting the allocation question entirely with a bounded ring buffer that touches no allocator at all in steady state, which has [a post of its own]({% post_url 2026-08-25-the-ring-that-doesnt-allocate-benchmarking-a-bounded-mpmc-queue %}).

The first attempt lost 83 percent of the items pushed through it, and my test suite told me it was fine. That is this post.

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

I built it on a 64-core AMD EPYC (Rome) VM, ran `make test-queue`, and got twenty-seven passes out of twenty-seven. Single-threaded FIFO, balanced four-by-four, fan-out, fan-in, count and checksum invariants, all green.

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

Line up the three machines I ran this on and the numbers refuse to agree. At 8P x 1C the Xeon corrupts 4 trials in 20 and ARM corrupts 11, while the EPYC VM where I first hit this corrupts every one. The balanced 8P x 8C mix is spotless on the Xeon across 20 trials, breaks once on ARM, and breaks 3 times in 20 on the EPYC. Totalled across all seven mixes: 13 of 140, 19 of 140, 46 of 140.

One source file, one bug, and the odds of catching it swing by a factor of three with nothing but the hardware. "Our test suite is green on CI" would have been a statement about the CI machine's core count and memory system rather than about the code. A stress test samples a probability distribution a handful of times, and the distribution moves when you change machines. The Xeon's clean 8P x 8C row is not evidence that balanced workloads are safe, only evidence that this Xeon did not happen to hit the window in twenty tries.

The broken variant is still in the repo, marked as broken and excluded from the test suite, because a reproducer for ABA that fails on demand turns out to be hard to find and useful to have.

## Making the Pointer Change

The fix is to make the head word change value on every modification, so a returning pointer no longer compares equal to itself. The usual approach pairs the pointer with a counter and compare-exchanges both together, which on x86-64 means a 128-bit `cmpxchg16b` and on AArch64 means the LSE `CASP` instruction. Both need `-march` flags, and this lab deliberately compiles with identical flags on both architectures so the cross-architecture comparison stays clean.

There is a cheaper trick. On both x86-64 and AArch64, Linux hands out user-space addresses under 2^47 by default, so the top sixteen bits of any pointer we own are zero and available to borrow:

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

That assert is not decoration. "Pointers are 48 bits" describes a default, not the architecture: x86-64 with [5-level paging](https://docs.kernel.org/arch/x86/x86_64/5level-paging.html) supports 57-bit addresses, and AArch64 with LVA supports 52-bit ones. Linux keeps allocations under the 47-bit boundary on both unless a program explicitly asks for more via an `mmap` hint, largely so that pointer-tagging code like this keeps working. Request a high address anywhere in the process and the packing corrupts silently, which is why the check runs on every node instead of living in a comment.

Sixteen bits of version wraps every 65,536 modifications, so ABA is not eliminated, only made astronomically unlikely. An interfering thread would have to perform an exact multiple of 65,536 free-list operations inside another thread's compare-exchange window. A 128-bit tagged pointer removes the wrap; hazard pointers or epoch-based reclamation remove the whole class of problem by making it safe to touch a node that might have been recycled. The repo implements none of those, and if you need a guarantee rather than a probability you should reach for one of them instead of this. What I have survives thirty consecutive runs of the full test suite on both benchmark machines and forty on the EPYC box, which is enough to benchmark with and is not a proof.

That was the bar I needed, because the question this post started from is still open. Part 1's pools lost to plain `new`/`delete` with mutexes inside them, and a correct lock-free free list is the fair way to ask whether the mutexes were the reason. The [next post]({% post_url 2026-08-04-taking-the-lock-out-of-the-pool-and-building-a-ring-instead %}) runs it against all three earlier designs on the Xeon and on the ARM machine.

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes); ARM Neoverse-N1 (16 cores, single NUMA node).
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, identical on both architectures (no `-march`) for a clean cross-architecture comparison.
ABA rates: 20 trials per mix at 100k items per producer, reported as absolute deviation from the expected item count. The third machine cited is a 64-core AMD EPYC (Rome) VM running GCC 11.4.0, a different compiler from the other two, so its higher corruption rate is an extra data point and not a controlled comparison.

**Previously:** [A Deep Dive into Producer-Consumer Queues in C++]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %})

**Next:** [Taking the Lock Out of the Pool, and Building a Ring Instead]({% post_url 2026-08-04-taking-the-lock-out-of-the-pool-and-building-a-ring-instead %})
