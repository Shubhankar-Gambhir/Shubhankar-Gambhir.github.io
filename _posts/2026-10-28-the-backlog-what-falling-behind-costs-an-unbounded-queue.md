---
title: "The Backlog: What Falling Behind Costs an Unbounded Queue"
date: 2026-10-28
categories: [C++, Performance]
tags: [concurrency, queue, payload, memcpy, glibc, malloc, page-faults, ring-buffer, benchmarks, arm]
description: >-
  What a 4 KB queue payload compiles to, and what happens when consumers that copy it
  inside a lock fall behind. On two ARM machines with glibc 2.39, an unbounded queue grew
  until the kernel's OOM killer took it, while the bounded ring, which never asks the
  allocator for memory, was the fastest queue in every 4 KB mix on the Xeon.
---

Every queue in my lab copies each item twice: into storage the queue owns on push, and out to the consumer on pop. The queues differ in what they hold while they copy. `TwoLockQueue` moves the value out while it holds the head lock, so every other consumer waits through the copy; `TwoLockQueueDeferred` is the same queue with that move pushed past the unlock. The other three are a `std::deque` behind one mutex (`MutexQueue`), a linked list behind one mutex (`MutexLinkedQueue`), and a bounded lock-free ring.

On a two-socket Xeon, [moving that one copy out of the lock]({% post_url 2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue %}) made the two-lock queue 1.54x faster at 4 KB with four consumers, but only with glibc's heap trimming switched off. Under default settings, glibc 2.28's allocator was a coin flip: each process landed in a fast mode or one up to seven times slower, and that swamped the copy. The payloads are `std::array<char, N>` from 8 bytes to 4 KB, and "trimming off" here means `glibc.malloc.trim_threshold` set to 16 GB, plus a 1 GB `top_pad`.

## What the Copy Compiles To

The copy itself changes shape as N grows. Without `-march`, GCC 13.4 only has 16-byte SSE2 moves, so a 64-byte payload is four load/store pairs:

```nasm
move64(std::array<char, 64>&, std::array<char, 64>&):
        movdqu  (%rsi), %xmm0        # load bytes 0-15 of the source (unaligned)
        movups  %xmm0, (%rdi)        # store them to the destination
        movdqu  16(%rsi), %xmm1      # load bytes 16-31
        movups  %xmm1, 16(%rdi)      # store bytes 16-31
        movdqu  32(%rsi), %xmm2      # load bytes 32-47
        movups  %xmm2, 32(%rdi)      # store bytes 32-47
        movdqu  48(%rsi), %xmm3      # load bytes 48-63
        movups  %xmm3, 48(%rdi)      # store bytes 48-63
        ret                          # return to caller
```

The unrolling continues to 256 bytes (sixteen pairs). From 512 bytes up, GCC switches to a string instruction for the middle and patches the ends by hand:

```nasm
move4096(std::array<char, 4096>&, std::array<char, 4096>&):
        movq    (%rsi), %rax         # load the first 8 bytes of the source
        movq    %rdi, %rcx           # remember the original destination
        leaq    8(%rdi), %rdi        # destination + 8
        movq    %rax, -8(%rdi)       # store the first 8 bytes
        movq    4088(%rsi), %rax     # load the last 8 bytes of the source
        movq    %rax, 4080(%rdi)     # store the last 8 bytes
        andq    $-8, %rdi            # round the destination down to 8-byte alignment
        subq    %rdi, %rcx           # rcx = how far the alignment moved it (negative)
        subq    %rcx, %rsi           # move the source pointer by the same amount
        addl    $4096, %ecx          # bytes left for the bulk copy
        shrl    $3, %ecx             # convert to 8-byte words
        rep movsq                    # copy rcx words from (%rsi) to (%rdi)
        ret                          # return to caller
```

[See all five sizes on Compiler Explorer](https://godbolt.org/z/Pc6j9bbTq) (GCC 13.4, `-std=c++17 -O2`). The benchmark binary contains the same inlined `rep movsq` in its producer and consumer loops. `-O3` produces identical code for these functions.

The switch lands at 512 bytes, the same size at which libstdc++'s `std::deque` starts giving every element its own chunk (`__deque_buf_size` in [`stl_deque.h`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/bits/stl_deque.h;hb=releases/gcc-13.4.0#l91), 512-byte threshold), so `MutexQueue` changes two things at once there. I expected a cliff and didn't get one: at 4P x 4C on the Xeon, `MutexQueue` goes 1,053, 2,040, 2,896, 3,181 ns from 64 to 1024 bytes, with no jump at 512.

## Falling Behind

The ARM machines run Ubuntu 24.04 with glibc 2.39, and there the coin flip is mostly gone: only 13 of 170 cells at 2 KB and up have a wide spread between runs. Trimming still costs something. At 4 KB, default glibc makes `TwoLockQueue` 2.4 to 3.2x slower than with trimming off, and the deferred variant up to 4.4x, but the same cell lands on the same number run after run. Copy location is larger here, and the two instances agree closely (a median difference of 4.5 percent across all cells, 86 percent of cells within 10):

| ARM, 4 KB | TwoLock / Deferred, default | TwoLock / Deferred, trimming off |
|---|---:|---:|
| 4P x 4C (instance 1 / 2) | 1.42x / 1.40x | 2.64x / 2.55x |
| 8P x 8C (instance 1 / 2) | 1.34x / 1.32x | 2.22x / 1.91x |
| 15P x 1C at 2 KB (instance 1 / 2) | 1.12x / 1.01x | 1.04x / 1.11x |

Some of the ARM gap is present at 8 bytes already (1.41x and 1.18x at 4P x 4C on the two instances), so not all of it is the copy. The growth with N shows up with trimming off, where 4P x 4C goes from 1.41x at 8 bytes to 2.64x at 4 KB on instance 1; under default glibc, instance 1's 4 KB ratio (1.42x) is no bigger than its 8-byte one. I cannot tell you whether the difference between the two machines comes from the architecture or from glibc 2.28 against 2.39; they changed together, and I did not separate them.

Then there is the cell that would not finish. At sixteen producers and sixteen consumers with 4 KB items and trimming off, `TwoLockQueue` never wrote a result on either ARM instance. Reproduced by hand, the run climbed to 64.6 GB of resident memory in 38 seconds before the kernel's OOM killer took it. Sixteen consumers sharing sixteen cores with sixteen producers, each doing a 4 KB copy inside the one head lock, cannot drain as fast as producers can fill. When consumers are even slightly slower than producers, an unbounded queue has no steady state. It just grows. The deferred variant in the same cell on the same machine runs at 889 ns per item and fits in memory.

The same thing happens on the Xeon in fan-in, where one consumer faces fifteen producers. Peak memory for a single 15P x 1C run at 4 KB was 52 GB for `MutexQueue` and at least 57 GB for `MutexLinkedQueue` before I stopped measuring. That fits in the Xeon's 187 GB and does not fit in the ARM workers' 62 GB, which is why that one ARM cell is missing from the data. With default glibc the same `TwoLockQueue` cell finished on both instances, at 4.0 and 4.6 microseconds per item. My best explanation, which I have not measured, is that turning trimming off made the producers faster, because they stopped paying for page faults, and faster producers are exactly what turns "slow" into "killed." Bounding the queue removes this failure mode by making producers wait, which is the ring's whole trick.

## The Ring Does Not Care

Part 2 said a big payload would penalize the ring. At 4 KB it is the fastest queue in every Xeon mix in every mode, 486 to 1,437 ns, and its cost at four consumers rises from 344 ns at 8 bytes to 488 at 4 KB while `MutexQueue` goes up ninefold. On ARM it wins every 4 KB mix but one.

The ring copies exactly as much as everyone else. What it never does is ask for memory: its cells are allocated once, up front, and when they are all full the producers wait instead of growing a backlog. The allocator has nothing to trim or fault back in.

The exception is the oversubscribed one. At 16P x 16C on 16 ARM cores the ring takes 1,423 ns against the deferred variant's 889: a preempted thread holding a claimed cell stalls the sequence handoff for everyone behind it, where a mutex would let them sleep. The same dip is in [Part 2's ARM table]({% post_url 2026-08-21-the-ring-that-doesnt-allocate-benchmarking-a-bounded-mpmc-queue %}#the-ring) with `int64_t`: 763 ns at 16P x 16C, against 486 for the deque.

## Two Layers

Counting copies told me nothing; every queue makes two. Where the copy sits decides whether the threads waiting on a lock pay for it, and at 4 KB, with the allocator held still, that is worth 1.4 to 2.6x. That is enough to decide whether an unbounded queue keeps up or grows until the kernel kills it.

Underneath it is the allocator, and on glibc 2.28 it was the bigger effect by far. It turned a 1.5x difference into 7x in one process and erased it in the next, even with each variant running alone. On glibc 2.39 it still costs 2.4 to 4.4x at 4 KB, but it lands on the same number every time, which is what lets the copy location show through on ARM. If you benchmark anything allocation-heavy on glibc, give each variant its own process, and look at page faults before you believe a gap at large sizes.

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes, 187 GB, glibc 2.28 from the machine's stock OS, the same box as Parts 1 and 2); two ARM Neoverse-N1 cloud instances (16 cores, single NUMA node, 62 GB, Ubuntu 24.04, glibc 2.39). 16P x 16C is 32 threads on both, which oversubscribes ARM's 16 cores.
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, no `-march`, identical on both architectures, linked with `-static-libstdc++` so `std::string` runs each compiler's own runtime. No code-alignment flags, to match Parts 1 and 2. An `-O3` spot check (4P x 4C and 15P x 1C at 8, 512 and 4096 bytes) agreed with `-O2` within run-to-run spread.
Methodology: 1M items per producer, 5 runs after warmup, median reported, threads pinned to consecutive core ids (which interleaves the Xeon's two NUMA nodes), synchronized start, count and checksum invariant. Payloads up to 512 bytes come from a sweep with all variants in one process; from 1 KB up, every variant ran in its own process, with default glibc and with `GLIBC_TUNABLES=glibc.malloc.trim_threshold=17179869184:glibc.malloc.top_pad=1073741824`. An `int64_t` baseline through the new harness matched Part 2's Xeon table within about 10 percent except the 1P x 1C cells, which are noisy on this machine. ARM ran `std::array` only, on two instances. Missing ARM cells: 15P x 1C at 4 KB (backlog exceeds RAM) and `TwoLockQueue` at 16P x 16C, 4 KB, trimming off (out of memory).

**Previously:** [The Copy: What a Bigger Payload Does to a Concurrent Queue]({% post_url 2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue %})
