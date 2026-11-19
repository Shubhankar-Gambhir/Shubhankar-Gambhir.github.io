---
title: "Pick Two: What Your Queue Does When the Consumer Falls Behind"
categories: [C++, Concurrency]
tags: [concurrency, queue, producer-consumer, logging, backpressure, condition-variable, hotspot, jvm, openjdk]
description: >-
  Between 2020 and 2025 the JVM's asynchronous logger gave four answers to one
  question: what happens to a message when the buffer is full? Grow, drop the oldest,
  drop the newest, then let the user choose to stall. The review threads behind each
  answer, the bugs that sat on the full path, and a C++ two-buffer logger that writes
  its overload policy down.
---

Between 2020 and 2025, the JVM's asynchronous logger gave four different answers to one question: what happens to a log message when there is no room for it?

The first answer proposed for mainline was to make room (its author had used a ring buffer in JDK 8 and given it up), and it lasted ten days. The second threw away the oldest message, the third threw away the newest, and the fourth, which shipped in JDK 25, lets whoever starts the JVM choose between dropping and making the logging thread wait. Each switch was argued in public by the people who wrote and reviewed the code.

This is a history post. The numbers in it are quoted from those HotSpot engineers; I measured none of them. Where my own queue benchmarks bear on a claim, I link them.

<!-- AUTHOR: One or two true sentences on what sent you into this history (a logging stall you saw, a question from the earlier queue posts, the talk). Leave it out if there is nothing specific. -->

You need one fact about the JVM to follow along. HotSpot, the C++ program at the core of OpenJDK, sometimes parks every application thread so the garbage collector can work. That is a stop-the-world pause, with every thread stopped at what HotSpot calls a safepoint: picture them all blocked on one barrier until the collector lets them go. Anything the collector does while they wait, including writing a log line, makes the pause longer.

## The Disk Under the Pause

In August 2019 Ramki Ramakrishna asked hotspot-dev whether there was appetite for buffered logging. Back in the JDK 7 era, [he wrote](https://mail.openjdk.org/pipermail/hotspot-dev/2019-August/039130.html), "some folks noticed much longer than expected pauses that seemed to coincide with GC logging in the midst of a GC safepoint". The logs had gone to a disk file "rather than to a RAM-based tmpfs which had been the original design center assumption". His team had already built buffered logging into their own JDK 8 builds, as others had independently, and he wanted it upstream.

That is a producer-consumer queue, with every logging thread a producer and one background writer. Xin Liu took the proposal on, and his JDK 8 measurements already showed the trade. With an artificial 5 ms delay on every write, the synchronous build's 99th-percentile pause went from 0.486 s to 0.567 s, while ["delay5ms-async remains the same p99 and p100 at expense of losing 148k logging events"](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2020-February/038211.html).

## Ten Days of Growing

When Liu reposted the design against the current JDK in February 2020, he made it lossless:

> In this implementation, I decide to use dynamic memory. ie. It doesn't drop any message. It's because I observe that asynchronous task is so fast that it doesn't need much memory to buffer all messages in the interval.

Underneath, "A mutex-protected FIFO stores all asynchronous log messages." It was a linked list behind a lock, with no limit. The reasoning holds for as long as the consumer keeps up, which is the one situation where you don't need an overload policy at all. When the consumer falls even slightly behind, an unbounded queue has no steady state; in [my payload benchmarks]({% post_url 2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue %}) one reached 64.6 GB of resident memory in 38 seconds before the kernel's OOM killer took it.

Liu's internal users raised the same worry ten days later, and [his update](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2020-March/038495.html) conceded it: "Indeed, I gave up ringbuffer design in previous version and turn to use a dynamic allocated linkedlist. If writing to filesystem stalls infinitely, JVM may implode because of memory usage." The second answer was a memory quota: "It will start dropping early messages of the FIFO if it has consumed too many free memories."

## Pick Two

The design came back as a pull request in March 2021.

Thomas Stuefe set the terms on 27 March. He accepted loss: ["if we work with a limited flush buffer and we log too much, things will get dropped, that is unavoidable. But it has to be reliable and comprehensible after the fact."](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050373.html) He also said what reliable meant: "a log system which randomly omits log entries because some internal buffer is full without giving any indication *in the log itself* is a terrible idea :)".

Two days later David Holmes [questioned the bound itself](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050386.html): "Why can't the design accommodate unlimited buffer space as determined by available memory?"

[Liu's reply](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050459.html) gave this post its title:

> It can be proven that we cannot have all three guarantees at the same time: **non-blocking**, **bounded memory** and **log fidelity**.

Non-blocking was the whole point of the feature, so "we think it's fair to trade log fidelity for non-blocking".

The same day, Robbin Ehn offered a way to stop the lock from ever blocking a logger: ["If you instead use two arrays, one active and one for flushing, you can swap them with atomic stores from the flushing thread."](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050479.html) Loggers would claim slots with an atomic increment, and an RCU-style wait would tell the flusher when the old array was free.

Liu said no. His first objection was that RCU suits many readers and rare writers, and ["Unfortunately, all participants of logging are writers. No one is read-only."](https://mail.openjdk.org/pipermail/hotspot-dev/2021-April/050620.html) Ehn [pointed out](https://mail.openjdk.org/pipermail/hotspot-dev/2021-April/050663.html) that only the flusher writes the buffer pointer, so every logger counts as a reader. But the reason that decided it was in Liu's next paragraph:

> TBH, I would rather grumpy customers yell at me about async-logging performance first before jump into lock-free algorithms.

With a lock, "it is easy to be reviewed as well." Review is where lock-free code hurts: the first lock-free stack on this blog passed its tests once and then [failed forty reruns in a row]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}). Ehn let it go: "If you feel that your current code is battle-proven and you think doing additional enhancements as incremental changes is better, please do so."

## Which Messages to Lose

Which messages to lose came up in May 2021, in the compatibility review for the new `-Xlog:async` option ([CSR JDK-8264323](https://bugs.openjdk.org/browse/JDK-8264323)). The pull request still dropped the oldest, and Holmes questioned hard-wiring either policy: "it may be that the user needs to be able to decide which is better for them?" Liu defended the oldest: the "current policy simulates a RingBuffer", and "I think recent logging messages are more relevant when AsyncLog flusher thread get blocked."

Holmes argued from the reader's side: "The cons of dropping oldest is that it is the old log messages that may give you the clue as to why the log buffer filled up faster than it could be drained." Stuefe sided with him on the policy and against him on the choice. He wanted the dropped-message "gaps in the log related to the time the buffer was exhausted", and: "I would not leave this up to the user to choose."

Liu conceded in a line: "Indeed, no matter what kind of data struture, dropping incoming message is easier:)". The code switched to dropping the newest, with no user choice, and the feature was integrated on 27 May 2021, first appearing in build jdk-17+25.

## The Full Buffer, Twice

Within four weeks of integration, the full path broke.

In [JDK-8269064](https://bugs.openjdk.org/browse/JDK-8269064) Liu explained that each message was a `strdup` copy, and "when we decide to drop the message, we need to call os::free for the string, or memory leaks." Every dropped message leaked. The fix landed on 23 June, in jdk-17+28.

The second bug was filed five weeks after integration. A test run on AIX, where a semaphore tops out at 32k, failed an assertion inside `sem_post` ([JDK-8269865](https://bugs.openjdk.org/browse/JDK-8269865)). The consumer's wake-up signal was a counting semaphore. Every enqueue posted it once, but the writer drained the whole buffer per wake-up. Holmes spelled out what that does: "If you have N loggers you get N signals and N messages. The writer thread will write all N messages at once and then loop another N-1 times (redundantly calling write()) before blocking." The count only climbs that high when the writer is behind: "The problem _is_ that the async log thread can't keep up". The fix was to "replace two uses of Semaphore with one PlatformMonitor and so completely avoid any overflow issues", which in C++ terms is a mutex, a condition variable and a flag. It landed in jdk-17+31, and both fixes made JDK 17's GA release.

Neither bug could happen while the consumer was keeping up.

## Stall, as an Option

In 2022 Liu replaced the linked list with two pre-allocated buffers that the writer swaps (JDK-8292989, jdk-20+16). Allocating per message, [the description](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2022-August/057285.html) said, "has impact on glibc/malloc". The mutex stayed: "Please note this effort won't eliminate mutex lock."

In February 2024 Johan Sjölen [reported](https://mail.openjdk.org/pipermail/hotspot-dev/2024-February/084892.html) what that lock cost under heavy logging: contention "can lead the output thread to suffer from starvation, as it cannot take the mutex to swap the buffers. This in turn leads to excessive dropping of messages". He proposed "a basic producer/consumer pattern with two mutexes and a circular buffer", and a new answer to the full buffer: stall the logging thread until there's room. Stuefe, who in 2021 hadn't wanted users choosing a drop policy, now asked whether drop mode was needed at all: ["I never liked the idea of losing output"](https://mail.openjdk.org/pipermail/hotspot-dev/2024-February/084893.html).

Liu reran the comparison on a slow debug build, twenty times with a t-test: [the ring cut dropped messages](https://mail.openjdk.org/pipermail/hotspot-dev/2024-March/085961.html) by 86.8% writing to a file and 2.9% writing to stdout. Stalling was another matter: "why do we still choose async-logging in the first place?" Holmes, who had lost the argument for a user choice in 2021, [answered](https://mail.openjdk.org/pipermail/hotspot-dev/2024-March/085964.html) on 19 March:

> when the producers do outstrip the consumer then you have to decide what is more important: the log message, or the non-blocking behaviour? That is a policy choice the programmer should be allowed to make.

He cited the rejection policies of Java's `ThreadPoolExecutor` as precedent: aborting, "processing the item directly instead of enqueuing", "deleting the oldest item in the queue to make room", and dropping the new item.

The ring didn't survive. In December 2024 Sjölen posted a smaller patch that added stalling on top of the existing two buffers, and [explained](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2024-December/075170.html) that the first one "didn't go through because 1. The stalling mode was broken 2. The complexity was a bit too large imho." His benchmark, a Spring Boot hello-world app serving 100,000 requests with all logging at debug, came out at 15,441 requests per second stalling, 20,255 dropping and 3,584 synchronous. The stalling run wrote a 501 MiB log; the dropping run wrote 292 MiB.

Stall mode landed in February 2025, in jdk-25+12, as `-Xlog:async:stall`; dropping stayed the default. That July the async-logging stress test hung in CI ([JDK-8362282](https://bugs.openjdk.org/browse/JDK-8362282)); its stall-mode run uses `-XX:AsyncLogBufferSize=192`, a 192-byte buffer, and the core dump showed a logging thread waiting inside the enqueue path. Holmes: "Looks like some form of deadlock if you examine the native stacks from the core dump." The fix, in jdk-26+15, reordered the writer's start-up; the backport request says the problems "manifest at startup".

## Same VM, Opposite Answer

Java Flight Recorder (JFR) is a low-overhead event recorder in the same VM, closer to a kernel trace ring than to a logger, and it answers the full buffer the other way. Per [JEP 328](https://openjdk.org/jeps/328), when a thread's local buffer fills "it is promoted to a global in-memory circular buffer system which maintains the most recent event data", and "Depending on configuration, the oldest data is either discarded or written to disk". That sentence is the whole public rationale I found.

The drop path had its own full-buffer bug. From the [first JFR commit](https://github.com/openjdk/jdk/blob/a060be188df894ed5c26fc12fc9e902f9af32bd3/src/hotspot/share/jfr/recorder/storage/jfrStorage.cpp#L399) in 2018 (jdk-11+14), `discard_oldest` took `JfrBuffer_lock` with `try_lock()`, checked whether another thread had already made room, and if so returned with the lock still held. The symptom, a debug-build assertion, was tracked as [JDK-8244289](https://bugs.openjdk.org/browse/JDK-8244289). In July 2023 Markus Grönlund wrote, "I was finally able to pinpoint this strange problem." His [review request](https://mail.openjdk.org/pipermail/hotspot-jfr-dev/2023-July/005195.html) describes "an invalid lock acquisition when JFR is configured for not writing to disk", the one configuration where the discard path runs. The [fix](https://github.com/openjdk/jdk/commit/61932f49a58b73530e89e511874fbe2fbc343e99) (jdk-22+7) swapped the manual lock for a scoped try-lock, the HotSpot spelling of `std::unique_lock lk(m, std::try_to_lock)`.

## A Policy Outside the Queue

HotSpot's just-in-time compiler has a queue too: application threads enqueue methods that have become hot, and compiler threads turn them into machine code. The queue has no capacity limit, and by default no application thread waits on it. A request for a method that is [already queued](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/compiler/compileBroker.cpp#L1107-L1119) is skipped, and the rest of its overload policy lives upstream: as the queue grows, the thresholds for submitting a method rise, so producers offer less work. The [2011 header comment](https://github.com/openjdk/jdk/blob/2114527fec4758c483744322fbfb773d69ac754f/hotspot/src/share/vm/runtime/advancedThresholdPolicy.hpp#L37-L40) gives the reason: "The rationale is that by the time a method gets compiled it can become unused, so it doesn't make sense to put too much onto the queue."

In JDK 25 that feedback was bypassed by accident. Man Cao [reported](https://bugs.openjdk.org/browse/JDK-8368071) a large application that "may compile >750K methods in the first hour after startup with JDK 21. With JDK 25, it only compiles ~370K methods in the first hour." It took eight hours to warm up to where JDK 21 had been after one. A change for JEP 515, ahead-of-time method profiling (jdk-25+26), had switched to a threshold check without the logic "to adjust the scale based on code cache usage and compilation queue size"; the fix restored it in jdk-26+17. Neither the bug nor its review says why losing the feedback cut the number of compiled methods.

This transfers only where unsent work still gets done some slower way, as an uncompiled method still runs in the interpreter. In C++ it looks like a depth check before `push` that raises the producer's own threshold for what is worth sending.

## Writing Yours Down

`std::queue` over `std::deque` behind a `std::mutex`, the queue I measured in [the first queue post]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), already has an overload policy: grow. So does every unbounded queue. If you don't write a policy down, the container picks one for you. Here is the menu:

| Policy | The producer sees | Who pays | In this post |
|---|---|---|---|
| Abort | an error | the caller | Holmes's `ThreadPoolExecutor` list |
| Caller runs | its own slow write | the caller's latency | async logging's synchronous fallback |
| Drop oldest | success | readers of old data | JFR in memory; async logging until 2021 |
| Drop newest | failure | readers of new data | `-Xlog:async`, the default |
| Block | a wait | every producer | `-Xlog:async:stall` |
| Grow | success | memory | the February 2020 design |
| Coalesce | success, merged | detail | duplicate requests to the compile queue |
| Throttle the producer | less work to offer | the producer's throughput | the JIT compile queue |

HotSpot writes synchronously for threads that must not wait on the writer, such as the writer itself ([current code](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/logging/logAsyncWriter.cpp#L145)).

Choose between the two drop policies by asking who reads the data and what they need from it. Someone debugging an overload from a log needs the lines that came before it, which is Holmes's argument. Someone reading a flight recording usually wants the last few seconds. Either way, record the loss in-band, at the point of loss. A metrics counter says something went missing, but only a marker in the stream puts the gap between the lines it interrupted.

Reserve room for control messages, as HotSpot's [buffer code](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/logging/logAsyncWriter.cpp#L88) does: "Always leave headroom for the flush token. Pushing a token must succeed." If a flush or shutdown message could be dropped, the thread waiting for it would wait forever.

Wake a batching consumer on a predicate, `condition_variable::wait(lock, pred)` over a flag or a non-empty check, and not on a semaphore whose count tracks items it will drain in bulk. And test the full path on purpose, with a buffer small enough to fill. Every bug in this post was on that path, and HotSpot's own stress test found the stall hang with 192 bytes.

Library queues leave the policy, the loss record and the wake-up to you, and supply mechanisms instead: moodycamel::ConcurrentQueue's `enqueue` allocates where `try_enqueue` fails, boost.lockfree has the same split between `push` and `bounded_push`, and folly's `MPMCQueue` adds a `blockingWrite`.

## Why the Logger Kept Its Mutex

In five years, every structure that shipped or got close kept a lock. The 2021 list was guarded by a mutex, the 2022 buffers kept it, and Sjölen's ring had two. Lock-free was floated for the contention problem as well, and left there: on the semaphore bug, Holmes suspected the lock would be "a serialization point" and wrote, "We may need to look at a lock-free technique for that (not as part of this bug fix)." The only lock-free design anyone sketched was Ehn's, and I found no implementation or benchmark of it upstream. The best evidence that the lock cost something is Sjölen's starvation report and Liu's 86.8% fewer drops to a file, from a ring that still took mutexes and was withdrawn anyway, partly on complexity.

A lock-free log queue is for producers that may not lock, not for producers that are slow. In C++ that means a signal handler, a real-time audio thread, an allocator hook, or code that logs while holding a lock the consumer might need.

## A Logger That Writes Its Policy Down

Here is the design the history paid for, in fifty lines of C++17. The policy is an enum. Producers append to one buffer under a mutex; the single consumer swaps it for its own empty buffer and writes outside the lock. Each drop bumps a counter, and the consumer writes the count into the stream where the loss happened: after the batch for `DropNewest`, whose losses all came once the buffer was full, and before it for `DropOldest`, whose victims were the oldest messages since the last swap. The last slot is reserved, so `shutdown()` always fits.

```cpp
enum class OverloadPolicy { DropNewest, DropOldest, Block };

class TwoBufferLog {
    std::mutex m_;
    std::condition_variable ready_, room_;
    std::deque<std::optional<std::string>> active_, batch_;  // batch_: the consumer's buffer
    std::uint64_t dropped_ = 0;                       // losses since the last swap
    bool stop_ = false;
    const std::size_t slots_;
    const OverloadPolicy policy_;
    const std::function<void(std::string_view)> sink_;
public:
    TwoBufferLog(std::size_t slots, OverloadPolicy p, std::function<void(std::string_view)> sink)
        : slots_(slots < 2 ? 2 : slots), policy_(p), sink_(std::move(sink)) {}  // >= 1 usable
    bool push(std::string_view msg) {                 // any number of producers
        std::unique_lock lk(m_);
        if (policy_ == OverloadPolicy::Block)
            room_.wait(lk, [&] { return stop_ || active_.size() + 1 < slots_; });
        if (stop_) return false;
        if (active_.size() + 1 >= slots_) {           // full: the last slot is reserved
            ++dropped_;                               // counted at the point of loss
            if (policy_ == OverloadPolicy::DropNewest) return false;
            active_.pop_front();                      // DropOldest: evict, then append
        }
        active_.emplace_back(std::in_place, msg);
        ready_.notify_one();
        return true;
    }
    void shutdown() {                                 // a control message: never dropped
        std::lock_guard lk(m_);
        stop_ = true;
        active_.emplace_back(std::nullopt);           // always fits in the reserved slot
        ready_.notify_one();
        room_.notify_all();
    }
    void consumer_loop() {                            // exactly one consumer
        for (bool done = false; !done; batch_.clear()) {
            std::unique_lock lk(m_);
            ready_.wait(lk, [&] { return !active_.empty(); });  // a predicate, not a count
            batch_.swap(active_);
            std::uint64_t lost = std::exchange(dropped_, 0);
            lk.unlock();                              // write outside the lock
            room_.notify_all();
            std::string gap = "[" + std::to_string(lost) + " messages dropped]";
            if (lost && policy_ == OverloadPolicy::DropOldest) sink_(gap);  // lost before batch
            for (auto& m : batch_) { if (m) sink_(*m); else done = true; }  // nullopt: shutdown
            if (lost && policy_ == OverloadPolicy::DropNewest) sink_(gap);  // lost after batch
        }
    }
};
```

`push` returns `false` only when its own message was dropped, so under `DropOldest` it returns `true` while someone else's message pays. `Block` is the stall mode, with the catch HotSpot accepted: one slow write holds up every producer. The sketch also allocates a `std::string` per message, the cost HotSpot's 2022 buffers removed. It has been stress-tested under ThreadSanitizer, AddressSanitizer and UBSan: eight producers, 100,000 messages each, a deliberately slow consumer, every policy, and a two-slot run, each checking that delivered messages plus the in-stream drop counts equal the messages produced.

<!-- AUTHOR: The stress harness lives at tools/hotspot_series/sketches/pick-two-what-your-queue-does-when-the-consumer-falls-behind.cpp in the blog repo. Decide whether to link it, and whether you want "I ran it" wording once you have run it yourself. -->

---

Sources: HotSpot code is linked at `jdk-27-ga` (815ff4dc) where it is current, and at the commit where it lived otherwise: a060be188df for JFR's first discard path, 2114527fec4 for the 2011 compile policy. Versions are the first build tag containing each commit.

No benchmarks in this post: every number is quoted from the linked sources. The C++ sketch above is complete as shown; there is no companion repo.

**Next:** Two Commits, One Day: The Queue HotSpot Added and the Queue It Deleted (coming soon)
