---
title: "What Does a Lock Actually Cost? Benchmarking Concurrent Counters in C++"
date: 2026-06-24
categories: [C++, Performance]
tags: [concurrency, mutex, atomic, false-sharing, cache-line, memory-ordering, benchmarks, cpp20, arm, striped-counter]
description: >-
  Four concurrent counter implementations (mutex, atomic, and LongAdder-style striped)
  measured across thread counts on x86 and ARM. False sharing costs 31x. relaxed vs seq_cst
  costs nothing for RMW, and the disassembly explains why. Real numbers, real hardware.
---

How much does `std::mutex` actually cost? The textbook says "a syscall," the folklore says "atomics are always faster," and the conference talk you half-remember said something about lock-free. None of those is a number. I wanted the number, in nanoseconds, on real hardware.

I built four concurrent counter variants in C++17, added three more in C++20, and measured them across thread counts on an Intel Xeon Gold 6130 and an ARM Neoverse-N1. The results challenged several things I thought I knew about locking, memory ordering, and false sharing. One of them sent me into the disassembler to figure out why a difference I was certain I would see simply was not there.

The companion repo is [on GitHub](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp). Everything here is reproducible with `make bench`.

## The Setup

A concurrent counter is the simplest possible shared mutable state: multiple threads call `increment()`, and after they all finish, `get()` returns the total. Two methods, one integer, no surrounding data structure. That simplicity is the whole point. Whatever you measure is the synchronization mechanism, not the workload.

The four C++17 variants each pick a different strategy. The mutex version is the obvious baseline:

```cpp
class MutexCounter {
    mutable std::mutex _m;
    int64_t _counter = 0;
public:
    void increment() { std::lock_guard l(_m); _counter++; }
    int64_t get() const { std::lock_guard l(_m); return _counter; }
};
```

The atomic versions differ only in the memory order passed to `fetch_add`:

```cpp
class AtomicCounter {
    std::atomic<int64_t> _counter{0};
public:
    void increment() { _counter.fetch_add(1, std::memory_order_relaxed); }
    int64_t get() const { return _counter.load(std::memory_order_relaxed); }
};
// AtomicSeqCstCounter is identical but uses std::memory_order_seq_cst.
```

The striped counter is the interesting one. It borrows the idea behind Java's `LongAdder`: instead of one hot integer that every thread fights over, keep an array of cells, send each thread to its own cell, and sum them at the end. Correctness must not depend on which cell a thread lands in, only performance, so the slot assignment is a one-time-per-thread choice and `get()` reads are only exact once every writer has joined:

```cpp
template <typename T, std::size_t N = 64, bool Align = true>
class StripedCounter {
    struct alignas(Align ? hardware_destructive_interference_size
                         : alignof(T)) Cell { T counter; };
    std::array<Cell, N> _v;
    // each thread is handed a slot once, then reuses it
public:
    void increment() { _v[slot() % N].counter.increment(); }
    int64_t get() const { /* sum every cell */ }
};
```

That `bool Align` template parameter is deliberate. It lets me build the exact same data structure with and without cache-line padding and measure the difference, which turns out to be the most dramatic result in the whole post.

The harness creates T threads, pins each to a distinct core (Linux only; the call is a no-op on macOS), releases them all at once through an atomic go-flag so every thread starts in the same instant, and measures wall-clock time. Each configuration runs five times after a warmup; I report the median with min and max. After every run a post-run invariant check confirms `get() == threads * iterations`, because a concurrent benchmark with a silent race produces a number that looks perfectly plausible and is completely wrong.

### Hardware and Compiler

| | Intel Xeon Gold 6130 | ARM Neoverse-N1 |
|-|---------------------|-----------------|
| Cores | 2x16 (32 physical, 64 with HT) | 16 |
| Clock | 2.10 GHz | -- |
| Architecture | Skylake | ARMv8.2-A |
| Compiler | GCC 11.4.0 | GCC 13.3.0 |
| Flags | `-O2 -march=skylake-avx512` | `-O2` |

## The Uncontended Lock

At one thread there is zero contention. This isolates the cost of the primitive itself, with no cache-line tug of war to muddy it.

| Variant | x86 (ns/op) | ARM (ns/op) |
|---------|-------------|-------------|
| MutexCounter | 25.9 | 21.3 |
| AtomicCounter (relaxed) | 8.6 | 5.1 |
| AtomicSeqCstCounter | 8.6 | 5.1 |
| AtomicRefCounter (C++20) | 8.6 | 4.9 |

An uncontended `std::mutex` lock/unlock pair costs 26 ns on x86 and 21 ns on ARM. That is not a syscall. libstdc++ delegates to glibc's `pthread_mutex_lock`, whose fast path is a single compare-and-swap on the lock word in userspace; the kernel only gets involved when a thread actually has to sleep on contention. The 3x gap to a bare `fetch_add` is real, but 26 ns is still small. If your critical section does a hash lookup, copies a string, or touches the network, the lock is rounding error. Reach for lock-free only after you have measured the lock and found it wanting.

## The Contention Curve

The picture changes the moment two threads want the same cache line.

### x86 Xeon Gold 6130 (ops/sec)

| Variant | 1 | 2 | 4 | 8 | 16 | 32 |
|---------|---|---|---|---|----|----|
| MutexCounter | 38.7M | 6.7M | 5.1M | 7.0M | 6.9M | 6.9M |
| AtomicCounter (relaxed) | 115.8M | 16.1M | 20.8M | 24.4M | 27.0M | 29.1M |
| AtomicSeqCstCounter | 115.8M | 21.1M | 24.2M | 26.7M | 29.4M | 29.2M |

### ARM Neoverse-N1 (ops/sec)

| Variant | 1 | 2 | 4 | 8 | 16 | 32 |
|---------|---|---|---|---|----|----|
| MutexCounter | 47.0M | 13.1M | 9.9M | 9.0M | 9.8M | 9.1M |
| AtomicCounter (relaxed) | 196.2M | 108.5M | 102.9M | 82.6M | 83.5M | 100.7M |
| AtomicSeqCstCounter | 196.8M | 108.2M | 108.7M | 85.8M | 91.5M | 102.2M |

The mutex falls off a cliff at two threads. On x86 it drops from 38.7M to 6.7M, an 83% loss from a single competitor, and never recovers. The atomic variants degrade too, but the floor is far higher.

ARM absorbs contention better. At two threads `AtomicCounter` drops from 196M to 108M, a 45% loss, against x86's fall from 116M to 16M, an 86% loss. The reason is in the instructions. The Neoverse-N1 implements an atomic add as a load-exclusive / store-exclusive retry loop (`ldxr` / `stxr`) that fails and retries locally when another core touches the line, while x86's `lock xadd` must win exclusive ownership of the line through the coherence protocol before it can complete. The same logical operation maps onto very different hardware.

## relaxed vs seq_cst: Why They Cost the Same

Here is the result I was most sure I had wrong. `AtomicCounter` (relaxed) and `AtomicSeqCstCounter` post identical numbers at every thread count, on *both* architectures. I expected x86 to tie and ARM to diverge. ARM did not diverge. So I went to the disassembly.

On x86 the tie is no mystery. Both orders compile to one instruction ([Compiler Explorer](https://godbolt.org/z/43Y5vdKEh)):

```nasm
        lock xaddq  %rax, (counter)   # atomic read-modify-write, full barrier
```

The `lock` prefix on x86 is already a full barrier; sequential consistency for a read-modify-write comes for free with the prefix. The `memory_order` argument only tells the *compiler* what it may reorder around the instruction. The emitted opcode is the same for relaxed and seq_cst, so the cost is identical by construction.

ARM is where I expected to be vindicated. Compiling for the baseline `armv8-a` target, GCC 13 emits an out-of-line library call for each order ([Compiler Explorer](https://godbolt.org/z/j57eWPnx8)):

```nasm
_Z17fetch_add_relaxedv:
        mov     x0, 1
        add     x1, x1, :lo12:.LANCHOR0
        bl      __aarch64_ldadd8_relax      # ldxr / stxr loop, no ordering
_Z17fetch_add_seq_cstv:
        mov     x0, 1
        add     x1, x1, :lo12:.LANCHOR0
        bl      __aarch64_ldadd8_acq_rel    # ldaxr / stlxr loop, acquire+release
```

These genuinely are different routines. The relaxed one uses plain `ldxr` / `stxr` (load-exclusive, store-exclusive). The seq_cst one uses `ldaxr` / `stlxr`: load-acquire-exclusive and store-release-exclusive. With `-march=armv8.1-a`, where the Large System Extensions give a single-instruction atomic add, the same distinction shows up as a suffix ([Compiler Explorer](https://godbolt.org/z/xeMaTerxE)):

```nasm
f_relax:    ldadd    x1, x1, [x0]     # atomic add, no ordering
f_seq_cst:  ldaddal  x1, x1, [x0]     # atomic add, acquire (a) + release (l)
```

So the ordering is right there in the encoding. Why no measured difference? Because of what the ordering acts on. The acquire on the load stops *later* memory operations from being reordered before the RMW; the release on the store stops *earlier* memory operations from being reordered after it. In a tight loop whose entire body is one `fetch_add`, there are no surrounding loads or stores to hold back. The store-release-exclusive already has to make its result globally visible to complete the exclusive sequence, which is the expensive part, and the ordering guarantees are left fencing against operations that do not exist. The ordering is encoded but has nothing to enforce.

The lesson is not "memory ordering is free on ARM." It is "memory ordering is free for a *read-modify-write with nothing around it*." The difference reappears the instant you use standalone loads and stores instead of an RMW. The same probe shows it plainly ([Compiler Explorer](https://godbolt.org/z/j57eWPnx8)):

```nasm
store relaxed:  str   x0, [x1]    # plain store
store seq_cst:  stlr  x0, [x1]    # store-release
load  relaxed:  ldr   x0, [x0]    # plain load
load  seq_cst:  ldar  x0, [x0]    # load-acquire
```

`str`/`ldr` and `stlr`/`ldar` are different instructions with different costs, and there a relaxed-versus-seq_cst benchmark would finally separate. That is the natural next experiment: a producer-consumer where one thread writes data then a release flag, and the other reads the acquire flag then the data. There, the ordering is not just measurable, it is required for correctness; dropping to relaxed on both sides is a data race, not an optimization. The counter loop simply never gave the ordering anything to do.

## The False Sharing Tax

The striped counter exists to dodge the contention curve: give each thread its own cell and the threads stop fighting. But the cells must sit on separate cache lines. Put two cells on the same 64-byte line and two threads that look independent are quietly ping-ponging that line between their cores. This is false sharing, and the `Align` template parameter lets me price it exactly.

### x86 (ops/sec, 32 threads)

| Variant | Aligned | Unaligned | Penalty |
|---------|---------|-----------|---------|
| Striped\<Atomic\> | 2,459M | 79M | **31x** |
| Striped\<Mutex\> | 1,023M | 49M | **21x** |

### ARM (ops/sec, 32 threads)

| Variant | Aligned | Unaligned | Penalty |
|---------|---------|-----------|---------|
| Striped\<Atomic\> | 2,380M | 397M | **6x** |
| Striped\<Mutex\> | 658M | 215M | **3x** |

On x86 the aligned striped counter sustains 2.5 billion increments per second at 32 threads. The unaligned version, byte-for-byte the same logic minus the `alignas(64)` on the cell, collapses to 79 million. A 31x cliff, and the only thing that changed is where the cells land in memory.

ARM's penalty is milder, 6x rather than 31x, and the reason is a nice detail. GCC 13 on the Neoverse-N1 reports `std::hardware_destructive_interference_size` as 256 bytes, four times the 64-byte cache line, because the core's prefetcher works on a wider spatial granularity. The unaligned cell still gets `alignof(int64_t)`, which is 8 bytes, so on x86 eight cells crowd into one 64-byte line and thrash, while on ARM the wider effective granularity spaces accesses out enough to soften the blow. One standard library constant quietly encodes a different architectural assumption, and the same source code inherits it.

The flip side is the aligned scaling, which is close to linear. Each thread owns its line, so adding a core adds throughput with no cross-core traffic at all: 1,296M ops/sec at 16 threads, 2,459M at 32. Almost double, almost free. Stripe-and-pad is the single highest-leverage move in this whole post.

## C++20: What Is Worth the Upgrade

Three C++20 features, measured against the C++17 baseline.

### std::atomic_ref: zero overhead

`AtomicRefCounter` wraps a plain `int64_t` with `std::atomic_ref` instead of declaring a `std::atomic<int64_t>`. On both architectures the numbers match `AtomicCounter` exactly at every thread count. The wrapper compiles away to nothing. This is the feature to reach for when you cannot change the storage type itself: a field in a struct that came from a C library, a slice of a memory-mapped region, a member of a class hierarchy you do not own. You get atomic semantics over existing bytes at no cost.

There is one ergonomic snag. `atomic_ref<T>` takes a non-const `T&`, so using it inside a `const` method forces either a `mutable` member or a `const_cast`. The standard offers no read-only atomic view, because even a load can participate in a release sequence, which needs non-const access to the object's synchronization state. It reads as a wart until you remember that an atomic load is not a passive read.

### atomic::notify_one: not free

`WaitNotifyCounter` calls `_counter.notify_one()` after every `fetch_add`. Nobody is waiting; the point is to price the call itself.

| | x86 (1 thread) | ARM (1 thread) |
|-|----------------|-----------------|
| AtomicCounter | 115.8M ops/s | 196.2M ops/s |
| WaitNotifyCounter | 59.7M ops/s | 81.8M ops/s |
| **Overhead** | **48%** | **58%** |

`notify_one` pokes the futex layer on every call, whether or not a waiter exists, and that roughly halves throughput. In a real producer-consumer you notify only when you have reason to think someone is blocked, or you batch notifications. Calling it unconditionally in a hot path is a self-inflicted wound.

### ModernStripedCounter: same speed, cleaner code

The C++20 striped counter uses `atomic_ref` over plain `int64_t` cells rather than wrapping each cell in an atomic class. Performance matches the C++17 version on both platforms; the code loses a wrapper type and reads a little cleaner. The codegen is equivalent, so this is purely an ergonomic win. Take it if you are already on C++20.

## GCC Version Sweep

Same benchmark, three GCC versions, x86 only.

| Variant (32 threads) | GCC 9 | GCC 11 | GCC 13 |
|---------------------|-------|--------|--------|
| MutexCounter | 4.6M | 6.9M | 5.6M |
| AtomicCounter | 30.5M | 29.1M | 29.0M |
| Striped\<Atomic, aligned\> | 2,535M | 2,459M | 2,405M |

The atomic and striped numbers sit within noise across every version, because the compiler emits the same `lock xadd` and there is nothing left to optimize. The mutex swings by half, from 4.6M on GCC 9 to 6.9M on GCC 11, and that is not codegen. It is the glibc bundled with each compiler's conda environment. The mutex fast path bottoms out in glibc's `__lll_lock_wait`, and glibc has tuned its spin-before-sleep heuristics over the years. For a mutex, the runtime you link against matters more than the compiler that built you.

## What I Learned

The counter is the first data structure in a synchronization benchmark lab I am building, and it set the priors for everything that follows.

The uncontended lock is cheap, around 26 ns, so if the critical section does real work the lock is not your bottleneck. False sharing is the silent killer: a 31x drop from one missing `alignas`, with no warning and no error, just a slow number that masquerades as ordinary contention, so any per-thread array gets cache-line padding. Memory ordering, for a read-modify-write with nothing around it, is free on both x86 and ARM, and the disassembly says why; the cost only appears once standalone loads and stores enter the picture, which is the next experiment. `std::atomic_ref` is genuinely free and worth using wherever it cleans up the code. And `notify_one` is not free, costing roughly half your throughput when fired into an empty room, so it belongs only where a waiter might actually be listening.

Next up in the lab: a concurrent queue, where the contention is not on a single integer but on the head and tail of a shared structure, and where lock-free finally has something to prove.

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake); ARM Neoverse-N1 (16 cores).
Compilers: GCC 11.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-O2`, `-march=skylake-avx512` (x86), C++17 and C++20.
Methodology: 1M iterations per thread, 5 runs after warmup, median reported, synchronized start, post-run invariant check. Assembly captured with `-O2 -S`; ARM LSE forms shown with `-march=armv8.1-a`.
