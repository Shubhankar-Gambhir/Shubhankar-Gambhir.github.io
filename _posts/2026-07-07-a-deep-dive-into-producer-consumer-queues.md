---
title: "A Deep Dive into Producer-Consumer Queues in C++"
date: 2026-07-07
categories: [C++, Performance]
tags: [concurrency, queue, mutex, michael-scott, two-lock-queue, object-pool, malloc, false-sharing, numa, benchmarks, arm]
description: >-
  Six MPMC queue variants measured across producer/consumer mixes on x86 and ARM.
  Splitting one lock into two is 6.8x faster for fan-out and slower for fan-in. A deque
  beats a linked list 5x uncontended. And the object pool we added to skip malloc made
  things worse. Real numbers, real hardware.
image:
  path: /assets/img/og/a-deep-dive-into-producer-consumer-queues.png
  alt: "A Deep Dive into Producer-Consumer Queues in C++"
  hero: false
---

The [counter post]({% post_url 2026-06-24-what-does-a-lock-actually-cost-concurrent-counter-benchmarks %}) ended with a promise: next up is a queue, where the contention is no longer a single integer but the head and tail of a shared structure. This is that post. I built six multi-producer multi-consumer queues in C++17, from a mutex wrapped around `std::queue` to a Michael-Scott two-lock design with a sharded node pool, and measured every one across nine producer/consumer mixes on an Intel Xeon Gold 6130 and a 16-core ARM machine.

Splitting a queue's single lock into separate head and tail locks makes fan-out 6.8x faster and fan-in slower. A deque-backed queue is 5x faster than a linked list when uncontended and the worst of the six under fan-out. And the object pool I added to eliminate per-node `malloc` made the queue slower on both machines, for a reason that says something uncomfortable about hand-rolled pools.

The companion repo is [on GitHub](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp). Everything here reproduces with `make bench-queue`.

## The Contract

Every variant implements the same two methods:

```cpp
bool push(const T& v);   // enqueue; returns false only when full (bounded variants)
bool pop(T& out);        // dequeue into out; returns false when empty
```

None of the six variants here is bounded, so their `push` always returns `true`. That is the whole surface. Everything interesting is behind it.

The benchmark spins up P producer threads and C consumer threads, each pinned to its own core, handed out as consecutive core ids. That detail matters on the Xeon: its two NUMA nodes are interleaved by core id (even ids on one socket, odd on the other), so any run with more than one thread already straddles both sockets. Producers push one million items each; consumers pop until every item is accounted for. A synchronized start releases all threads at once, the clock covers only the timed region, and a post-run invariant checks that the count and the checksum match what went in, so a variant that loses or duplicates an item scores zero rather than a fast-but-wrong number. I report nanoseconds per item, median of five runs after a warmup.

The mixes are the point. A balanced mix (P equals C) measures throughput under symmetric load. A fan-out mix (one producer, many consumers) starves the queue: consumers outrun the single producer and spend most of their time finding it empty. A fan-in mix (many producers, one consumer) floods it: the lone consumer cannot keep up. Real systems live in all three regimes, and the best queue for one is the worst for another.

## One Lock, Two Data Structures

The obvious baseline is a mutex around the standard library's queue:

```cpp
template <typename T>
class MutexQueue {
    std::mutex _m;
    std::queue<T> _q;
public:
    bool push(const T& v) {
        std::lock_guard l(_m);
        _q.push(v);
        return true;
    }
    bool pop(T& out) {
        std::lock_guard l(_m);
        if (!_q.empty()) {
            out = std::move(_q.front());
            _q.pop();
            return true;
        }
        return false;
    }
};
```

`std::queue` sits on a `std::deque`, which allocates in chunks and keeps elements contiguous within each chunk. To see what that buys you, the control is the same single lock over a hand-rolled linked list, where every `push` is a separate `new` and every `pop` a `delete`:

```cpp
template <typename T>
class MutexLinkedQueue {
    std::mutex _m;
    NodeNA<T>* head;
    NodeNA<T>* tail;
public:
    bool push(const T& v) {
        NodeNA<T>* n = new NodeNA<T>(v);
        std::lock_guard l(_m);
        tail->next = n;
        tail = n;
        return true;
    }
    bool pop(T& out) {
        NodeNA<T>* old;
        {
            std::lock_guard l(_m);
            auto n = head->next;
            if (n == NULL) return false;
            out = std::move(n->value);
            old = head;
            head = n;
        }
        delete old;
        return true;
    }
    // dummy-node constructor and drain destructor omitted
};
```

Both hold one lock. The only difference is the container underneath. Uncontended, on the Xeon, that difference is enormous:

| Variant (1P x 1C) | x86 ns/item | ARM ns/item |
|-------------------|------------:|------------:|
| MutexQueue (deque)     | 186 | 221 |
| MutexLinkedQueue       | 960 | 484 |

Five times slower on x86, a bit over two times on ARM. The linked list pays a `malloc` and a `free` on every single item, and it chases a pointer to a node that lives wherever the allocator put it, which is rarely next to the last one. I am bundling two effects here, allocation cost and cache locality, and I did not separate them, so the gap is motivation rather than a controlled attribution.

## Two Locks

A producer and a consumer never touch the same end of the queue, yet with one mutex they wait for each other anyway. The [Michael-Scott two-lock queue](https://www.cs.rochester.edu/research/synchronization/pseudocode/queues.html) fixes that by giving the head and the tail their own locks:

```cpp
template <typename T>
class TwoLockQueue {
    std::mutex _h;   // consumers only
    std::mutex _t;   // producers only
    Node<T>* head;
    Node<T>* tail;
public:
    bool push(const T& v) {
        Node<T>* n = new Node<T>(v);
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
            out = std::move(n->value);
            old = head;
            head = n;
        }
        delete old;
        return true;
    }
};
```

The design leans on a permanent dummy node: `head` always points at a node whose `value` is stale, and the real front is `head->next`. A producer only ever touches `tail`; a consumer only ever touches `head`; they meet at exactly one place, the `next` pointer of the node at the boundary when the queue is nearly empty. That single shared field is why `next` is a `std::atomic` with a release store in `push` and an acquire load in `pop`. The release-acquire pair is the handoff: it guarantees that when a consumer reads a newly published `next`, it also sees the fully constructed node behind it.

## The Measurement

Here is the two-lock queue against the single-lock linked list on the Xeon:

| Mix | MutexLinkedQueue (1 lock) | TwoLockQueue (2 locks) | Speedup |
|-----|--------------------------:|-----------------------:|--------:|
| 1P x 8C (fan-out)  | 3946 ns | 822 ns  | 4.8x |
| 1P x 15C (fan-out) | 5974 ns | 873 ns  | 6.8x |
| 8P x 8C (balanced) | 1266 ns | 1273 ns | 1.0x |
| 8P x 1C (fan-in)   | 592 ns  | 1107 ns | 0.5x |
| 15P x 1C (fan-in)  | 612 ns  | 1130 ns | 0.5x |

The two-lock design is a rout under fan-out and a liability under fan-in.

With one lock, every consumer that wants to check for work must take that lock, find the queue empty, and release it, and there are eight or fifteen of them doing this in a tight loop. The lone producer, trying to enqueue, has to win the same lock against that entire mob of failing pops. Split the lock and the consumers pile up on `_h`, where they can fail to their hearts' content, while the producer owns `_t` outright and enqueues at full speed. Hence the 6.8x.

Fan-in is where it backfires, and the reason is that the two-lock design's benefit and its cost come due at different times. Its benefit is decoupling: keeping the consumers from starving the producer. Under fan-in there is no starvation to prevent, because the lone consumer always finds work and the many producers serialize on `_t` exactly as they would on a single lock. The cost, though, is always present: a second lock on its own cache line plus the atomic `next` handoff, extra coordination that has to move between threads on every operation.

The cross-architecture data tells you that cost is the socket crossing. On the single-NUMA-node ARM machine, the two-lock queue wins fan-in too (321 ns versus 378 ns at 15P x 1C), because moving a lock line between cores within one node is cheap. The fan-in penalty appears only on the two-socket Xeon, where every contended line has to cross the interconnect between NUMA nodes. I did not confirm this with performance counters; the socket-crossing account is an inference from the ARM control. The throughput numbers themselves are solid and repeatable.

The deque baseline makes the same point from the other direction. Under fan-out it collapses:

| Mix | MutexQueue (deque) x86 |
|-----|-----------------------:|
| 1P x 1C  | 186 ns  |
| 1P x 8C  | 2301 ns |
| 1P x 15C | 3493 ns |

The fastest structure in the lab, twelve times slower once the access pattern turns against its single lock. The data structure and the lock granularity are independent axes, and optimizing one tells you nothing about the other.

The fan-out win holds on ARM too, with the volume turned down: 534 ns versus 1957 ns at 1P x 8C, about 3.7x, rather than the Xeon's 4.8x.

## The Pooling Detour

Back to that linked-list allocation cost. The textbook fix is an object pool: keep a free list of retired nodes and reuse them instead of returning memory to the allocator. It is one of the first optimizations anyone reaches for, and I reached for it too.

Because a producer allocates a node and a consumer frees it, the free list is touched from both ends, so it needs its own synchronization. That is a third lock:

```cpp
Node<T>* getNode() {
    {
        std::lock_guard l(_f);          // free-list lock
        if (freeList) {
            Node<T>* n = freeList;
            freeList = freeList->next.load(std::memory_order_relaxed);
            n->next.store(NULL, std::memory_order_relaxed);
            return n;
        }
    }
    return new Node<T>();               // pool empty, fall back to malloc
}

void freeNode(Node<T>* n) {
    std::lock_guard l(_f);
    n->next.store(freeList, std::memory_order_relaxed);
    freeList = n;
}
```

`push` calls `getNode` instead of `new`; `pop` calls `freeNode` instead of `delete`. The queue's own locks are untouched. In principle this trades a pair of allocator calls for a pair of pointer swaps under a lock, which should be cheaper. It is not:

| Mix | TwoLockQueue | TwoLockQueuePooled | ARM: TwoLockQueue | ARM: Pooled |
|-----|-------------:|-------------------:|------------------:|------------:|
| 1P x 1C   | 962 ns  | 968 ns  | 282 ns | 577 ns |
| 1P x 8C   | 822 ns  | 966 ns  | 534 ns | 836 ns |
| 1P x 15C  | 873 ns  | 1062 ns | 568 ns | 783 ns |
| 16P x 16C | 1233 ns | 1242 ns | 750 ns | 1030 ns |

Pooling made it slower, and on ARM it is not close. The reason is the third lock. Every `push` and every `pop` now takes `_f`, unconditionally, so two threads that would never have contended on `_h` or `_t` now serialize on the free list. I set out to remove an allocator call and instead added a global lock that sits on the hot path of every single operation.

Glibc's allocator keeps [per-thread arenas](https://sourceware.org/glibc/wiki/MallocInternals): same-sized allocations from one thread hit that thread's own arena and rarely touch a shared lock at all. The allocator is, in effect, already sharded per thread. My hand-rolled free list is a single shared structure guarded by one mutex, which is a strictly worse concurrency design than the thing it was replacing. (This is a glibc claim; tcmalloc and jemalloc take different routes to the same end, so the specific numbers would shift, but the shape would not.)

The single-lock linked queue makes the point cleanest. Pooling it costs 12 percent at 1P x 1C but 93 percent at 1P x 15C, where fifteen consumers all return nodes through the one free-list lock. A penalty that stays small when threads work alone and explodes precisely under fan-out, when a crowd converges on `_f`, is the signature of lock contention, not allocation cost.

If the diagnosis is right, then splitting the free list should recover the loss, the same way splitting the queue's lock did. So I sharded it: sixteen free lists, each with its own lock, each `alignas(64)` so two shards never share a cache line, and a thread hashes its id to pick one. On ARM that confirms the diagnosis:

| Mix (ARM) | TwoLockPooled (1 free list) | TwoLockSharded (16 free lists) |
|-----------|----------------------------:|-------------------------------:|
| 1P x 1C   | 577 ns | 370 ns |
| 1P x 8C   | 836 ns | 673 ns |
| 1P x 15C  | 783 ns | 660 ns |

Reduce contention on the free-list lock and the penalty shrinks, which is as direct a confirmation as this dataset offers that the lock was the problem all along. But note the destination: even the sharded pool at 370 ns still loses to the plain `new`/`delete` two-lock queue at 282 ns.

On the Xeon the pooling picture is muddier. The penalty is still there in the fan-out and low-contention mixes (873 ns to 1062 ns at 1P x 15C, a 22 percent tax), but sharding on x86 ranges from neutral to actively harmful, since spreading sixteen aligned shards across two sockets invites exactly the cross-socket traffic the padding was meant to prevent.

## One Suspect Left

There is one suspect left standing. If a shared, locked free list loses to `malloc`, and a sharded, locked one still loses, the question is whether the lock is the only problem or whether pooling itself is a dead end for this workload. The way to find out is to take the lock away entirely: a lock-free free list built on a compare-and-swap loop, and then a bounded ring buffer that does no allocation at all in steady state and holds no lock on the hot path. That is the next post, where lock-free finally has something to prove.

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes); ARM Neoverse-N1 (16 cores, single NUMA node).
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, identical on both architectures (no `-march`) for a clean cross-architecture comparison.
Methodology: 1M items per producer, 5 runs after warmup, median reported, each thread pinned to its own core by consecutive core id (which interleaves the Xeon's two NUMA nodes, so multi-thread runs span both sockets), synchronized start, post-run count and checksum invariant.

**Previously:** [What Does a Lock Actually Cost? Benchmarking Concurrent Counters in C++]({% post_url 2026-06-24-what-does-a-lock-actually-cost-concurrent-counter-benchmarks %})

**Next:** [Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %})
