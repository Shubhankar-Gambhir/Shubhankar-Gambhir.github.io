---
title: "The Copy and the Backlog: What a Bigger Payload Does to a Concurrent Queue"
categories: [C++, Performance]
tags: [concurrency, queue, payload, memcpy, glibc, malloc, page-faults, ring-buffer, benchmarks, arm]
description: >-
  Part 2 predicted that a bigger payload would punish the ring buffer. It did the
  opposite. Every queue in the lab copies the payload twice; where those copies sit
  decides whether consumers keep up, and the allocator decides what falling behind
  costs, up to a sevenfold slowdown on one glibc and an out-of-memory kill on another.
---

Every queue in this lab copies your payload exactly twice, and the number that matters is not two.

The [previous post]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}) ended with a prediction. All the measurements so far moved an `int64_t`, which "flatters the linked variants, which hand around pointers to nodes, and penalizes the ring, which copies values into cells." A payload that costs real money to copy should move the ranking. So I made the payload expensive, from 8 bytes up to 4 KB, and measured five queues on the same two machines.

The ranking moved, but not the way Part 2 said it would, and the reason sits two layers down. The first layer is where each copy happens relative to a lock. The second is what the allocator does when consumers fall behind, and on one of the two machines that second layer is worth seven times more than the first.

Everything here reproduces from the [companion repo](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp) with `scripts/run_part3.sh`.

## A Correction to Part 2

The linked queues do not hand around pointers. Here is `TwoLockQueue`, the Michael-Scott design from [Part 1]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), unchanged:

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

`TwoLockQueueDeferred` is the experiment. It is `TwoLockQueue` with the move pushed out past the unlock, which creates a lifetime problem: once the head lock is released, the next consumer can unlink and free the node we are still reading. The fix gives each node two references, one for "its value has been read" and one for "it has been unlinked as the old dummy," and whichever thread drops the last one deletes it:

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

Payloads are `std::array<char, N>` for N from 8 to 4096, plus `std::string` of the same lengths as a control. Each producer stamps a sequence number into the first eight bytes so the existing count-and-checksum invariant still works. Consumers pass the popped object's address through an empty `asm volatile` with a memory clobber, which forces the compiler to keep the full copy without making the consumer read N bytes itself. A guard target checks that, at one producer and one consumer, a 4 KB item costs more than twice an 8-byte one for every variant, so a silently elided copy fails the build rather than producing a fast number.

Queues now live on the heap (a 4 KB-payload ring is 4 MB), and everything else matches Parts 1 and 2: same nine thread mixes, 1M items per producer, median of five runs. The footer has the full setup, including one thing I got wrong in the first sweep and had to redo.

## Layer One: Where the Copy Happens

Four consumers, four producers, on the Xeon. Payloads up to 512 bytes come from the main sweep; 1 KB and up are measured with glibc's heap trimming (handing freed memory back to the kernel) turned off, for reasons the section after next is about.

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

The deferred variant stays flat at 1.1 to 1.3 microseconds while `TwoLockQueue` climbs, and the gap opens with N, reaching 1.54x at 4 KB. At sixteen consumers it is 1.41x at 4 KB. `MutexQueue`, which does both copies inside its one lock, gets worse from the very first size step: going from 8 to 64 bytes roughly doubles its cost, because every extra byte lengthens the only critical section in the queue.

Two controls say this is about the copy and not about the deferred variant being generally quicker. With one consumer there is nobody else waiting on the head lock, and the two variants come out even through 2 KB (1.04x, 0.97x). At 4 KB the deferred variant is actually slower with one consumer, 1,449 ns against 1,015. I profiled it: both spend over half their cycles in the kernel's futex path, with the fifteen producers fighting over the tail lock, and the deferred variant takes about 20 percent more context switches. I could not find the cause, and I am not going to invent one.

The second control is `std::string`. Its move is a pointer swap of about 32 bytes regardless of length, so moving it out of the lock should buy nothing, and from 1 KB up, where every variant ran in its own process, it buys between 0.96x and 1.11x. The string also lets you move the push-side cost instead. With a `push(T&&)` overload the producer hands its buffer to the queue instead of the queue allocating and copying a new one, and the effect depends entirely on where that copy used to sit:

| 4 KB string | `push(const T&)` | `push(T&&)` |
|---|---:|---:|
| MutexQueue, 4P x 4C | 2964 | 2285 |
| MutexQueue, 15P x 1C | 3623 | 1632 |
| TwoLockQueue, 4P x 4C | 1444 | 1362 |
| TwoLockQueue, 15P x 1C | 885 | 926 |

`MutexQueue` copies on push inside its lock, so taking the copy away helps by up to 2.2x. `TwoLockQueue` already copies before it takes the lock, so the same change is noise. The signature change only pays off where it takes work out of a critical section.

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

The switch lands at 512 bytes, the same size at which libstdc++'s `std::deque` starts giving every element its own chunk (`__deque_buf_size` in [`stl_deque.h`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/bits/stl_deque.h;hb=releases/gcc-13.4.0#l91), 512-byte threshold), so `MutexQueue` changes two things at once there. I expected a cliff. The curves keep their slope straight through it: `MutexQueue` goes 1,053, 2,040, 2,896, 3,181 ns from 64 to 1024 bytes. The instruction explains what a copy costs, not where the big steps are.

## Layer Two: The Allocator Underneath

The big steps were in the first sweep, and they were not about copying. At 4 KB and four consumers that sweep had `TwoLockQueue` at 6,507 ns and the deferred variant at 1,273, a 5.1x gap; at sixteen consumers it was 7.2x. Then I ran the deferred variant on its own, in a shorter run, and got 6,874 ns.

The original harness ran all five variants back to back in one process. With glibc's default settings, freed memory at the top of a heap is handed back to the kernel once it passes a threshold that starts at 128 KB and [adjusts itself to the program](https://sourceware.org/glibc/manual/2.28/html_node/Memory-Allocation-Tunables.html), and the next allocation faults it back in. At 4 KB per node that costs about one page fault per item. Whether a run settles into that pattern is not a property of the variant: it shifted with whatever had run earlier in the same process, and, as it turned out, it varies from one process to the next even when a variant runs alone.

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

That last column is the honest version of layer one, and it is why the table in the previous section uses it. The middle column is what you would actually see in production on glibc 2.28, which is to say a coin flip. I did not try jemalloc, tcmalloc or mimalloc; they make different trimming decisions, so this layer may look very different under them.

## Falling Behind

The ARM machines tell the same story with the layers reweighted. They run Ubuntu 24.04 with glibc 2.39, and the coin flip is mostly gone: only 13 of 170 cells at 2 KB and up have a wide spread between runs. Trimming still costs something. At 4 KB, default glibc makes `TwoLockQueue` 2.4 to 3.2x slower than with trimming off, and the deferred variant up to 4.4x, but the same cell lands on the same number run after run. Copy location is larger here, and the two instances agree closely (a median difference of 4.5 percent across all cells, 86 percent of cells within 10):

| ARM, 4 KB | TwoLock / Deferred, default | TwoLock / Deferred, trimming off |
|---|---:|---:|
| 4P x 4C (instance 1 / 2) | 1.42x / 1.40x | 2.64x / 2.55x |
| 8P x 8C (instance 1 / 2) | 1.34x / 1.32x | 2.22x / 1.91x |
| 15P x 1C at 2 KB (instance 1 / 2) | 1.12x / 1.01x | 1.04x / 1.11x |

Some of the ARM gap is present at 8 bytes already (1.41x and 1.18x at 4P x 4C on the two instances), so not all of it is the copy, but the part that grows with N is. I cannot tell you whether the difference between the two machines comes from the architecture or from glibc 2.28 against 2.39; they changed together, and I did not separate them.

Then there is the cell that would not finish. At sixteen producers and sixteen consumers with 4 KB items and trimming off, `TwoLockQueue` never wrote a result on either ARM instance. Reproduced by hand, it reached 64.6 GB of resident memory in 38 seconds and the kernel's OOM killer took it. Sixteen consumers sharing sixteen cores with sixteen producers, each doing a 4 KB copy inside the one head lock, cannot drain as fast as producers can fill. When consumers are even slightly slower than producers, an unbounded queue has no steady state. It just grows. The deferred variant, same cell, same machine: 889 ns per item, and it fits.

The same thing happens on the Xeon in fan-in, where one consumer faces fifteen producers. Peak memory for a single 15P x 1C run at 4 KB was 52 GB for `MutexQueue` and at least 57 GB for `MutexLinkedQueue` before I stopped measuring. That fits in the Xeon's 187 GB and does not fit in the ARM workers' 62 GB, which is why that one ARM cell is missing from the data. With default glibc the same `TwoLockQueue` cell finished on both instances, at 4.0 and 4.6 microseconds per item. My best explanation, which I have not measured, is that turning trimming off made the producers faster, because they stopped paying for page faults, and faster producers are exactly what turns "slow" into "killed." Bounding the queue removes this failure mode by making producers wait, which is the ring's whole trick.

## The Ring Does Not Care

Part 2 said a big payload would penalize the ring. At 4 KB it is the fastest queue in every Xeon mix in every mode, 486 to 1,437 ns, and its cost at four consumers rises from 344 ns at 8 bytes to 488 at 4 KB while `MutexQueue` goes up ninefold. On ARM it wins every 4 KB mix but one.

The ring copies exactly as much as everyone else. What it never does is ask for memory: its cells are allocated once, up front, and when they are all full the producers wait instead of growing a backlog. The allocator has nothing to trim or fault back in, so the second layer never reaches it.

The exception is the oversubscribed one. At 16P x 16C on 16 ARM cores the ring takes 1,423 ns against the deferred variant's 889: a preempted thread holding a claimed cell stalls the sequence handoff for everyone behind it, where a mutex would let them sleep. The same dip is in [Part 2's ARM table]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}#the-ring) with `int64_t`: 763 ns at 16P x 16C, against 486 for the deque.

## What I Learned

Counting copies told me nothing; every queue makes two. Where the copy sits decides whether the threads waiting on a lock pay for it, and at 4 KB that is worth 1.4 to 2.6x, enough to decide whether an unbounded queue keeps up or grows until the kernel kills it. That much matches the idea I started with.

What I did not expect was how much the second layer could hide or exaggerate the first. On one glibc, the allocator turned a 1.5x effect into a 7x effect in one run and erased it in the next, depending on what an unrelated variant had done to the heap earlier in the same process. The methodology lesson is blunt: with glibc, never benchmark several allocation-heavy variants in one process and compare them, and check page faults before believing any gap at large sizes.

[Part 1]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}) found that a locked node pool could not beat plain `malloc`, and Part 2 found that the best fix was not to allocate at all. This time the copy and the allocator both turned out to matter, and the queue that does best is still the one that never gives the allocator anything to do.

---

Companion repo: [github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp](https://github.com/Shubhankar-Gambhir/concurrent-data-structures-cpp)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake, two NUMA nodes, 187 GB, glibc 2.28 from the machine's stock OS, the same box as Parts 1 and 2); two ARM Neoverse-N1 cloud instances (16 cores, single NUMA node, 62 GB, Ubuntu 24.04, glibc 2.39). 16P x 16C is 32 threads on both, which oversubscribes ARM's 16 cores.
Compilers: GCC 13.4.0 (x86), GCC 13.3.0 (ARM). Flags: `-std=c++17 -O2 -Wall -Wextra -pthread`, no `-march`, identical on both architectures, linked with `-static-libstdc++` so `std::string` runs each compiler's own runtime. No code-alignment flags, to match Parts 1 and 2. An `-O3` spot check (4P x 4C and 15P x 1C at 8, 512 and 4096 bytes) agreed with `-O2` within run-to-run spread.
Methodology: 1M items per producer, 5 runs after warmup, median reported, threads pinned to consecutive core ids (which interleaves the Xeon's two NUMA nodes), synchronized start, count and checksum invariant. Payloads up to 512 bytes come from a sweep with all variants in one process; from 1 KB up, every variant ran in its own process, with default glibc and with `GLIBC_TUNABLES=glibc.malloc.trim_threshold=17179869184:glibc.malloc.top_pad=1073741824`. An `int64_t` baseline through the new harness matched Part 2's Xeon table within about 10 percent except the 1P x 1C cells, which are noisy on this machine. ARM ran `std::array` only, on two instances. Missing ARM cells: 15P x 1C at 4 KB (backlog exceeds RAM) and `TwoLockQueue` at 16P x 16C, 4 KB, trimming off (out of memory). The single-consumer slowdown of `TwoLockQueueDeferred` on the Xeon is measured but unexplained.

**Previously:** [Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %})
