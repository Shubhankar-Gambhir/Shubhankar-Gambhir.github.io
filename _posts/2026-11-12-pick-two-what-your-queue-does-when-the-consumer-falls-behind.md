---
title: "Pick Two: What Your Queue Does When the Consumer Falls Behind"
date: 2026-11-12
categories: [C++, Concurrency]
tags: [concurrency, queue, producer-consumer, logging, backpressure, condition-variable, hotspot, jvm, openjdk]
description: >-
  Between 2020 and 2025 the JVM's asynchronous logger gave four answers to one
  question: what happens to a message when the buffer is full? Grow, drop the oldest,
  drop the newest, then let the user choose to stall. The review threads behind each
  answer, the bugs that sat on the full path, and a C++ two-buffer logger that writes
  its overload policy down.
image:
  path: /assets/img/og/pick-two-what-your-queue-does-when-the-consumer-falls-behind.png
  alt: "Pick Two: What Your Queue Does When the Consumer Falls Behind"
  hero: false
---

My last queue post ended with an unbounded queue growing until the kernel killed it ([The Backlog]({% post_url 2026-11-04-the-backlog-what-falling-behind-costs-an-unbounded-queue %})). The obvious fix is to bound it, which just moves the question: once it's full, what gives?

Between 2020 and 2025, the JVM's asynchronous logger gave four answers. The first, proposed for mainline, was to make room, and it lasted ten days. The second threw away the oldest message, the third the newest, and the fourth, which shipped in JDK 25, lets whoever starts the JVM choose between dropping and making the logging thread wait.

This is a history post: the numbers are quoted from the HotSpot engineers who wrote and reviewed the code, and I measured none of them except where I link my own queue benchmarks.

HotSpot, the C++ program at the core of OpenJDK, sometimes parks every application thread at what it calls a safepoint so the garbage collector can work. Anything the collector does during that stop-the-world pause, including writing a log line, makes it longer.

## The Disk Under the Pause

In August 2019 Ramki Ramakrishna asked hotspot-dev whether there was appetite for buffered logging. Back in the JDK 7 era, [he wrote](https://mail.openjdk.org/pipermail/hotspot-dev/2019-August/039130.html), "some folks noticed much longer than expected pauses that seemed to coincide with GC logging in the midst of a GC safepoint". The logs had gone to a disk file "rather than to a RAM-based tmpfs which had been the original design center assumption".

That is a producer-consumer queue, with every logging thread a producer and one background writer. Xin Liu took the proposal on, and his JDK 8 measurements already showed the trade. With an artificial 5 ms delay on every write, the synchronous build's 99th-percentile pause went from 0.486 s to 0.567 s, while ["delay5ms-async remains the same p99 and p100 at expense of losing 148k logging events"](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2020-February/038211.html).

## Ten Days of Growing

When Liu reposted the design against the current JDK in February 2020, he made it lossless:

> It doesn't drop any message. It's because I observe that asynchronous task is so fast that it doesn't need much memory to buffer all messages in the interval.

Underneath, "A mutex-protected FIFO stores all asynchronous log messages." It was a linked list behind a lock, with no limit. The reasoning holds only while the consumer keeps up, when you need no overload policy at all. Once the consumer falls behind, an unbounded queue has no steady state; mine reached 64.6 GB of resident memory in 38 seconds.

Liu's internal users raised the same worry ten days later, and [his update](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2020-March/038495.html) conceded it: "If writing to filesystem stalls infinitely, JVM may implode because of memory usage." The second answer was a memory quota: "It will start dropping early messages of the FIFO if it has consumed too many free memories."

## Pick Two

The design came back as a pull request in March 2021.

Thomas Stuefe set the terms on 27 March. He accepted loss from a bounded buffer: ["things will get dropped, that is unavoidable. But it has to be reliable and comprehensible after the fact."](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050373.html) He also said what reliable meant: dropping entries "without giving any indication *in the log itself* is a terrible idea :)".

Two days later David Holmes [questioned the bound itself](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050386.html): "Why can't the design accommodate unlimited buffer space as determined by available memory?"

[Liu's reply](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050459.html) gave this post its title:

> It can be proven that we cannot have all three guarantees at the same time: **non-blocking**, **bounded memory** and **log fidelity**.

Non-blocking was the whole point of the feature, so "we think it's fair to trade log fidelity for non-blocking".

The same day, Robbin Ehn offered a way to stop the lock from ever blocking a logger: ["If you instead use two arrays, one active and one for flushing, you can swap them with atomic stores from the flushing thread."](https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050479.html) Loggers would claim slots with an atomic increment, and an RCU-style wait would tell the flusher when the old array was free.

Liu said no, first because RCU suits many readers and rare writers, and ["Unfortunately, all participants of logging are writers. No one is read-only."](https://mail.openjdk.org/pipermail/hotspot-dev/2021-April/050620.html) Ehn [pointed out](https://mail.openjdk.org/pipermail/hotspot-dev/2021-April/050663.html) that only the flusher writes the buffer pointer, so every logger counts as a reader. The reason that decided it was in Liu's next paragraph:

> TBH, I would rather grumpy customers yell at me about async-logging performance first before jump into lock-free algorithms.

With a lock, "it is easy to be reviewed as well." Ehn let it go.

## Which Messages to Lose

The question came up in May 2021, in the compatibility review for the new `-Xlog:async` option ([CSR JDK-8264323](https://bugs.openjdk.org/browse/JDK-8264323)). The pull request still dropped the oldest, and Holmes questioned hard-wiring either policy: "it may be that the user needs to be able to decide which is better for them?" Liu defended the oldest: "I think recent logging messages are more relevant when AsyncLog flusher thread get blocked."

Holmes argued from the reader's side: "it is the old log messages that may give you the clue as to why the log buffer filled up faster than it could be drained." Stuefe sided with him on the policy and against him on the choice. He wanted the dropped-message "gaps in the log related to the time the buffer was exhausted", and: "I would not leave this up to the user to choose."

Liu conceded in a line: "Indeed, no matter what kind of data struture, dropping incoming message is easier:)". The code switched to dropping the newest, with no user choice, and the feature was integrated on 27 May 2021, in jdk-17+25.

## The Full Buffer, Twice

Within four weeks of integration, the full path broke.

In [JDK-8269064](https://bugs.openjdk.org/browse/JDK-8269064) Liu explained that each message was a `strdup` copy, and "when we decide to drop the message, we need to call os::free for the string, or memory leaks." The fix landed on 23 June, in jdk-17+28.

The second bug was filed five weeks after integration. A test run on AIX, where a semaphore tops out at 32k, failed an assertion inside `sem_post` ([JDK-8269865](https://bugs.openjdk.org/browse/JDK-8269865)). The consumer's wake-up signal was a counting semaphore. Every enqueue posted it once, but the writer drained the whole buffer per wake-up. With N loggers, Holmes wrote, "The writer thread will write all N messages at once and then loop another N-1 times (redundantly calling write()) before blocking." The count only climbs that high when the writer is behind: "The problem _is_ that the async log thread can't keep up". The fix was to "replace two uses of Semaphore with one PlatformMonitor", which in C++ terms is a mutex, a condition variable and a flag. It landed in jdk-17+31, and both fixes made JDK 17's GA release.

## Stall, as an Option

In 2022 Liu replaced the linked list with two pre-allocated buffers that the writer swaps (JDK-8292989, jdk-20+16). Allocating per message, [the description](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2022-August/057285.html) said, "has impact on glibc/malloc". The mutex stayed: "Please note this effort won't eliminate mutex lock."

In February 2024 Johan Sjölen [reported](https://mail.openjdk.org/pipermail/hotspot-dev/2024-February/084892.html) what that lock cost under heavy logging: contention "can lead the output thread to suffer from starvation, as it cannot take the mutex to swap the buffers. This in turn leads to excessive dropping of messages". He proposed "a basic producer/consumer pattern with two mutexes and a circular buffer", and a new answer to the full buffer: stall the logging thread until there's room.

Liu reran the comparison on a slow debug build, twenty times with a t-test: [the ring cut dropped messages](https://mail.openjdk.org/pipermail/hotspot-dev/2024-March/085961.html) by 86.8% writing to a file and 2.9% writing to stdout. Stalling was another matter: "why do we still choose async-logging in the first place?" Holmes, who had lost the argument for a user choice in 2021, [answered](https://mail.openjdk.org/pipermail/hotspot-dev/2024-March/085964.html) on 19 March:

> when the producers do outstrip the consumer then you have to decide what is more important: the log message, or the non-blocking behaviour? That is a policy choice the programmer should be allowed to make.

He cited the rejection policies of Java's `ThreadPoolExecutor`, from aborting to dropping the new item, as precedent.

The ring didn't survive. In December 2024 Sjölen posted a smaller patch that added stalling on top of the existing two buffers, and [explained](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2024-December/075170.html) that the first one "didn't go through because 1. The stalling mode was broken 2. The complexity was a bit too large imho." His Spring Boot benchmark, with all logging at debug, ran at 15,441 requests per second stalling, 20,255 dropping and 3,584 synchronous; stalling wrote 501 MiB of log against 292 MiB.

Stall mode landed in February 2025, in jdk-25+12, as `-Xlog:async:stall`; dropping stayed the default. That July its stress test, running stall mode with a 192-byte buffer, hung in CI with a logging thread waiting inside the enqueue path ([JDK-8362282](https://bugs.openjdk.org/browse/JDK-8362282)); the fix, in jdk-26+15, reordered the writer's start-up.

## Writing Yours Down

`std::queue` over `std::deque` behind a `std::mutex`, the queue I measured in [the first queue post]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), already has an overload policy: grow. Here is the menu:

| Policy | The producer sees | Who pays | In this post |
|---|---|---|---|
| Abort | an error | the caller | Holmes's `ThreadPoolExecutor` list |
| Caller runs | its own slow write | the caller's latency | async logging, for [the writer's own messages](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/logging/logAsyncWriter.cpp#L145) |
| Drop oldest | success | readers of old data | async logging until 2021 |
| Drop newest | failure | readers of new data | `-Xlog:async`, the default |
| Block | a wait | every producer | `-Xlog:async:stall` |
| Grow | success | memory | the February 2020 design |

Choose between the two drop policies by asking who reads the data: someone debugging an overload from a log needs the lines that came before it, while someone watching a live trace usually wants the last few seconds. Either way, record the loss in-band: a metrics counter says something went missing, but only a marker in the stream puts the gap between the lines it interrupted.

Reserve room for control messages, as HotSpot's [buffer code](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/logging/logAsyncWriter.cpp#L88) does: "Always leave headroom for the flush token. Pushing a token must succeed."

Wake a batching consumer on a predicate (`condition_variable::wait(lock, pred)`), not a semaphore count. And test the full path on purpose, with a buffer small enough to fill: every bug in this post was on that path.

Library queues supply mechanisms and leave the policy to you: moodycamel::ConcurrentQueue's `enqueue` allocates where `try_enqueue` fails, boost.lockfree splits `push` from `bounded_push`, and folly's `MPMCQueue` adds a `blockingWrite`. spdlog's async logger does name its policy: [v1.17.0](https://github.com/gabime/spdlog/blob/v1.17.0/include/spdlog/async_logger.h#L21-L27) offers `block` (the default), `overrun_oldest` and `discard_new`. It counts losses in [`overrun_counter()` and `discard_counter()`](https://github.com/gabime/spdlog/blob/v1.17.0/include/spdlog/details/thread_pool.h#L92-L95), so the log shows no gap.

## Why the Logger Kept Its Mutex

In five years, every structure that shipped or got close kept a lock. On the semaphore bug Holmes wrote, "We may need to look at a lock-free technique for that", and I found no implementation or benchmark of Ehn's sketch upstream. Even Liu's 86.8% fewer drops to a file came from a ring that still took mutexes.

A lock-free log queue is for producers that may not lock, not for producers that are slow. In C++ that means a signal handler, a real-time thread, or code that logs while holding a lock the consumer needs.

## A Logger That Writes Its Policy Down

Here is the design the history paid for, in fifty lines of C++17. Each drop bumps a counter, and the consumer writes the count into the stream where the loss happened: after the batch for `DropNewest`, before it for `DropOldest`.

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
            std::string gap = lost ? "[" + std::to_string(lost) + " messages dropped]" : "";
            if (lost && policy_ == OverloadPolicy::DropOldest) sink_(gap);  // lost before batch
            for (auto& m : batch_) { if (m) sink_(*m); else done = true; }  // nullopt: shutdown
            if (lost && policy_ == OverloadPolicy::DropNewest) sink_(gap);  // lost after batch
        }
    }
};
```

`push` returns `false` only when its own message was dropped, so under `DropOldest` it returns `true` while someone else's message pays. The sketch also allocates a `std::string` per message, the cost HotSpot's 2022 buffers removed. It has been stress-tested with GCC 14.3.0 under ThreadSanitizer, AddressSanitizer and UBSan, with eight producers and a deliberately slow consumer, checking that delivered messages plus the in-stream drop counts equal the messages produced. The [stress harness](https://github.com/Shubhankar-Gambhir/Shubhankar-Gambhir.github.io/blob/main/tools/hotspot_series/sketches/pick-two-what-your-queue-does-when-the-consumer-falls-behind.cpp) is in the blog's repo.

---

Sources: HotSpot code is linked at `jdk-27-ga` (815ff4dc). Versions are the first build tag containing each commit.

No benchmarks here: every number is quoted from the linked sources. The sketch omits its `#include`s; the linked harness compiles as is.

**Next:** Two Commits, One Day: The Queue HotSpot Added and the Queue It Deleted (coming soon)
