---
title: "Two Commits, One Day: The Queue HotSpot Added and the Queue It Deleted"
categories: [C++, Concurrency]
tags: [concurrency, queue, producer-consumer, condition-variable, lock-free, hotspot, jvm, openjdk]
description: >-
  On 29 September 2020 one HotSpot engineer pushed two commits 46 minutes apart. The
  first turned two fixed slots into a queue with a lock-free push; the second turned
  the VM thread's twenty-year-old queue into a single slot. The review threads behind
  both, the bug that came from reading "empty" as "done", and a C++ one-slot
  rendezvous that counts the traffic it assumes.
---

On 29 September 2020, Robbin Ehn pushed two commits to the JVM, 46 minutes apart. The first turned two fixed slots into a queue. The second turned a queue into one slot.

Both first appear in build jdk-16+18 and both are still in JDK 27. They went opposite ways because their consumers needed opposite things.

Like Pick Two, this post is history: every number in it is quoted from the HotSpot engineers who wrote and reviewed the code, and none of them is mine.

<!-- AUTHOR: One or two true sentences on why these two commits caught your eye (reading the handshake code, the talk, a question from Pick Two). Leave it out if there is nothing specific. -->

HotSpot, the C++ program at the core of OpenJDK, has one privileged thread, the VM thread, that executes *VM operations*: requests from other threads for work that usually needs every application thread parked at a *safepoint*, the barrier behind a stop-the-world pause. A *handshake* is a closure run against one thread, by that thread or on its behalf, the next time it checks in. (HotSpot for C++ Programmers, coming soon, gives the longer tour.)

## Starved by a Steady Stream

VM operations used to wait in a queue with two priorities, one linked list each, and the VM thread served the high list first. Then a test hung: one thread kept producing high-priority operations while another thread's request to discard compiled code waited in the low list. [JDK-4390175](https://bugs.openjdk.org/browse/JDK-4390175), in JBS (the OpenJDK bug tracker), says "This steady stream of SafepointPriority operations effectively causes the lower priority VM_DeoptimizeFrame operation to be starved." Hui Huang found a Linux scheduling bug that made it worse, then named the queue's own flaw in May 2001: "A steady stream of high priority operations can lock out lower priority operations."

The queue's fix, for JDK 1.4, was a counter in `remove_next()`: on every eleventh pick, look at the low list first. In C++ that's two `std::deque`s and a `picks % 11` check. When OpenJDK's public history begins in 2007 the counter is there, citing the bug, and it lasted until 2020.

## Fire and Forget

The queue also let a requester skip the wait. Two of a VM operation's four modes were non-blocking: the caller heap-allocated the operation, queued it and moved on, and as one call site's [comment](https://github.com/openjdk/jdk/blob/6230791e9ef5767745f14c46b51548c842ba6082/src/hotspot/share/runtime/synchronizer.cpp#L1001-L1006) put it, "The VMThread will delete the op when completed." That's `queue.push(new Op{...})` with a `delete` in the consumer, plus an ownership rule every caller must get right.

In November 2019 Ehn proposed removing those modes ([JDK-8234086](https://bugs.openjdk.org/browse/JDK-8234086)). With the CMS garbage collector gone, "This means all VM ops can be stack allocated and synchronous." One caller, deep in the VM's lock-object allocation, really couldn't wait. It kept the atomic flag that had guarded its heap-allocated operation, and now only the thread whose `flag.exchange(1)` returns 0 wakes the VM thread. David Holmes, reviewing on hotspot-runtime-dev (one of HotSpot's mailing lists), [doubted](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2019-November/037141.html) that this "really amounts to a simplification".

Then, agreeing with Dan Daugherty, he [made the case](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2019-November/037173.html) for keeping fire-and-forget: making the WatcherThread, the VM's periodic-task thread, wait for a safepoint "could interfere with executing other periodic tasks" (if a non-default startup delay was set), so "Perhaps the ability to execute an async-safepoint VM operation needs to remain, for simplicity (compared to working around the issues)." [Ehn answered with numbers](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2019-November/037175.html): "A periodic task have a minimum resolution of 10ms, while the safepoint for enabling biased locking takes <1ms under normal circumstances." (Biased locking was a locking optimization, since removed.) Holmes [let it go](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2019-November/037234.html) without quite agreeing: "But it is a one-time effort so ..."

The change shipped in jdk-14+26, and from then on every requester waited for its answer.

## Once in a Blue Moon

Then there was batching. When several safepoint operations were waiting, the VM thread ran them all in one safepoint, the way a consumer swaps out a whole `std::deque` and drains it under one lock acquisition.

In June 2020 Erik Österlund asked for a way to let GC operations opt out ([JDK-8247179](https://bugs.openjdk.org/browse/JDK-8247179)), then [proposed deleting](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2020-June/040250.html) batching outright. "It's an optimization opportunity that almost never kicks in," he wrote. "Yet it has caused several bugs over the years where assumptions break when operations are coalesced once in a blue moon."

Holmes [agreed](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2020-June/040266.html) as its author ("I added that version of the coalescing code back in 2006") and reported a count: "Just out of interest I ran some tests to see if we presently encounter an actual queue of safepoint VM operations. It does still happen but may be an artefact of the tests involved", naming two tests, "but neither of these are a concern." The deletion shipped in jdk-16+3.

## One Slot

Batching was gone, and the priorities were next. Ehn had already written in the [bug](https://bugs.openjdk.org/browse/JDK-8212107) that once other subsystems stopped needing safepoints, "we almost only need GC safepoint. That makes the queue not needed."

His September 2020 pull request removed the priorities, the lists and the completion tickets. Holmes [asked](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043391.html) for the design in words, and [Ehn's answer](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043402.html) was about traffic: "VM operations now rare and when we do them they are now also faster compared to when the queue was introduced."

The replacement is [one pointer and one monitor](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/runtime/vmThread.cpp#L339-L366). A requester locks, waits while the slot is taken, installs a pointer to its own stack-allocated operation, notifies, and waits until "_next_vm_operation is not our op". The VM thread clears the slot only after running the operation, so one store means both "done" and "free". In C++ that's a `T*`, a `std::mutex` and a `std::condition_variable`, with nothing allocated.

Coleen Phillimore [asked](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043527.html) for "some counter while the thread is waiting to install its operation for logging and debugging purposes", because "the supposition is that there are few operations that cause safepoints so there's never a long wait." Ehn [replied](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043545.html), "Sounds like a good idea, fixing." The commit that shipped has no counter as such; it wraps the wait in a trace-level timer, `Installing VM operation`, and logs each failed attempt.

Dan Daugherty [worried](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043707.html) that "the waiting thread will be woken up more often and then go back to wait()ing without progress." He was right about the mechanism: each completion wakes every waiter with `notify_all`, and one wins. Holmes [answered](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043716.html) that this was the point: "we no longer need a queue because we rarely have >1 VM-operations in-flight." [Ehn](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043740.html) had checked:

> When I do stress test I have not been able to produce a thread printing "A VM operation already set, waiting" twice in a row.

The commit went in at 09:37 UTC.

## Forty-Six Minutes Earlier

The 08:50 commit, [JDK-8238761](https://github.com/openjdk/jdk/commit/6bddeb709d1d263d0d753909cabce7e755e7e27d), went the other way. Ehn had written HotSpot's handshakes in the first place, for JDK 10, and by 2020 each thread had [two pointer fields](https://github.com/openjdk/jdk/blob/6d19fe65d1c1d8f41fd2f18afd09de134912505f/src/hotspot/share/runtime/handshake.hpp#L66-L72) for pending handshakes, one per kind. That was enough while every requester waited. Asynchronous handshakes don't wait, and [his pull request](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043183.html) explained: "Asynchronous handshakes are target only executed, which they may never be executed. (target may block on socket for the rest of VM lifetime)". With one slot, [the bug](https://bugs.openjdk.org/browse/JDK-8238761) says, "an asynchronous handshake would thus block all other handshakes."

So each thread got a queue. "Inserts are done via CAS on first pointer, no lock needed. Pops are done while holding the per handshake state lock", and whoever held that lock popped and ran every operation it was allowed to take. In C++ that's a Treiber-style `compare_exchange` push onto one `std::atomic<Node*>` and a pop that walks the list under a `std::mutex`; the pointer is to the newest node, so the oldest is at the end of the walk.

Holmes [asked](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043394.html) about the backoff in push's CAS loop: "Was the need for spinwaits identified through benchmarking? Do you really expect this to be hot?" [Ehn](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043433.html): "No and no." Many pending handshakes would be "very rare", the pull request said, and [later](https://mail.openjdk.org/pipermail/hotspot-dev/2020-September/043826.html): "Since 99% of the time the queue is empty, we get the correct answers without needing to lock the handshake state lock." That lock-free empty check, `has_operation()`, returns in 2021.

The review never says why pop takes the lock instead of being lock-free too. My reading is that the popping thread already holds that lock to claim the target thread while the operation runs, so a lock-free pop would save nothing. Pop is also the only operation that frees a node, and it's serialized, so the queue has no ABA problem of the kind that [broke this blog's lock-free stack]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}).

The filter is the other half. The target thread took everything; other threads could take only synchronous operations, so "filtering causes handshake operation to be done out-order". The [class comment](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/utilities/filterQueue.hpp#L31-L34) says: "The FilterQueue is FIFO with the ability to skip over queued items." In C++, pop is a `std::find_if`-style walk that keeps the last match.

## Empty Means Safe

In May 2021 Man Cao wanted other threads to run asynchronous handshakes too ([JDK-8267079](https://bugs.openjdk.org/browse/JDK-8267079)), which meant widening the filter. Holmes [asked](https://mail.openjdk.org/pipermail/hotspot-dev/2021-May/052158.html), "I thought we had to preserve the order of handshake operations?" Man Cao [replied](https://mail.openjdk.org/pipermail/hotspot-dev/2021-May/052162.html) that with asynchronous and synchronous operations mixed, the queue already didn't. [Holmes](https://mail.openjdk.org/pipermail/hotspot-dev/2021-May/052163.html): "I had a mental model where order of ops was preserved. Introducing non-determinism in the order in which ops are executed seems potentially fragile". He proposed coalescing at enqueue instead, Ehn called the request a way to "handshake without a handshake :)", and the change was [dropped](https://bugs.openjdk.org/browse/JDK-8267079).

In the same weeks an assertion failure in testing, [JDK-8266557](https://bugs.openjdk.org/browse/JDK-8266557), reopened the queue itself. The executing thread used to pop an operation, run it, and loop. The target thread, without taking the lock, decided whether it could carry on by checking that the queue was empty and that nobody held the lock; the second check was unreliable. Holmes [wrote](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-May/048393.html) that "any use of Mutex::is_locked() has to be treated with suspicion as it can return false when the Mutex actually is locked." [Ehn's fix](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-May/048299.html) made emptiness carry the weight: "Fixed by leaving the handshakee being processed in queue until completed." Peek, execute, remove: in C++ terms `front()`, the work, then `pop_front()`, so anyone polling `empty()` still sees the item. The loop went too, "Since we have concurrent adds."

When Holmes [asked](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-May/048522.html) why it didn't just pop, [Ehn](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-May/048553.html) stated the rule: "If has_operation() returns false we continue out of the transition into an unsafe state. Thus has_operation() must return true until it is safe to do so." Daugherty [stopped his review](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-June/048855.html) over a `peek()` that "actually appears to be searching for a match rather than peeking at the head (or tail) of the queue", and [Ehn's answer](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-June/048913.html) summed up the 2020 design: "To provide lock-free inserts the queue only have a tail pointer." Then: "To get to the front of the queue we must always walk until the end."

Patricio Chilano Mateo [offered](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-June/048849.html) a smaller fix that kept the pop and the loop: "another simpler solution would have been to add a fence between claim_handshake() and can_process_handshake()". Ehn [refused](https://mail.openjdk.org/pipermail/hotspot-runtime-dev/2021-June/049018.html): "But that would be relying on implementation details of a generic queue." And: "If we are removing any ordering, we should remove it where it is not needed. (the CAS) Not the other way around!"

The fix shipped in jdk-17+26. Today's [handshake.cpp](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/runtime/handshake.cpp#L736) marks the call with `acquire, op removed after`.

## A Consumer That Ranks

HotSpot's just-in-time compiler has a queue too: application threads enqueue hot methods, and compiler threads compile them. (Pick Two covered how it throttles its producers.) In 2007 a compiler thread took the first task. Since 2011 the [policy header](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/compiler/compilationPolicy.hpp#L155-L159) has said: "When getting an element off the queue we pick the one with the largest rate." The rate tracks how hard the program still calls the method and changes while the task waits, so every pick [walks the whole list](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/compiler/compilationPolicy.cpp#L691-L737) under one global lock, rescoring tasks and unlinking stale ones. In C++: a `std::list`, a `std::mutex`, and `std::max_element` over a score computed at pop time. The record doesn't say why it's a scan rather than a heap. The 2011 [review request](https://mail.openjdk.org/pipermail/hotspot-compiler-dev/2011-March/004826.html) was one line and the [first reply](https://mail.openjdk.org/pipermail/hotspot-compiler-dev/2011-March/004828.html) was "Good." My reading is that a `std::priority_queue` would rank by scores frozen at push time.

[JDK-8320283](https://bugs.openjdk.org/browse/JDK-8320283), still open, says "all compile queue accesses are guarded by a single global lock" and "during application startup/warmup on large systems it's easy to run into contention", and proposes lock-free submission. Project Leyden, an OpenJDK effort on startup time, tried it on its own branch (`UseLockFreeCompileQueues`), dropping stale tasks as it moved submissions into the ranked list. Then a test hung with the compilers idle ([JDK-8348960](https://bugs.openjdk.org/browse/JDK-8348960)): the dropped tasks still had waiters, and nothing woke them "because the queue is empty, the compilers are idle, and we never get to CompileQueue::purge_stale_tasks(), which would never notify whoever is waiting on the task that task is stale."

## A Message That Means "There Is Work"

Java Flight Recorder (JFR), HotSpot's built-in event recorder, sends commands to its recorder thread through a post box. No public rationale is on record, so this is my reading of [jfrPostBox.cpp](https://github.com/openjdk/jdk/blob/815ff4dc327fe17f2433c7d115a5a503af10f3c4/src/hotspot/share/jfr/recorder/service/jfrPostBox.cpp#L87-L153). The mailbox is one `int` with a bit per kind of message. Posting ORs a bit in with a CAS loop, which stops early if "Some other thread just set exactly what this thread wanted"; the recorder drains everything with one exchange to zero. Ten posts of one kind before the recorder looks become one message, and nothing is lost, because the message only says there is work. A poster that needs no answer notifies only if a `try_lock` succeeds, so it never blocks. It's the 2019 monitor flag with a bit per kind. In C++:

```cpp
class FlagMailbox {                           // one bit per kind of "there is work"
    std::atomic<std::uint32_t> bits_{0};
    std::mutex m_;
    std::condition_variable cv_;
public:
    void post(std::uint32_t kinds) {          // never blocks
        bits_.fetch_or(kinds, std::memory_order_release);
        std::unique_lock lk(m_, std::try_to_lock);
        if (lk.owns_lock()) cv_.notify_all(); // a busy lock can cost a wake-up, never a bit
    }
    std::uint32_t collect(std::chrono::milliseconds tick) {  // one consumer
        std::unique_lock lk(m_);
        cv_.wait_for(lk, tick, [&] { return bits_.load(std::memory_order_acquire) != 0; });
        return bits_.exchange(0, std::memory_order_acquire);
    }
};
```

In the sketch, `collect`'s tick bounds how late a lost wake-up can make the consumer.

## Count Before You Choose

The VM-operation slot rested on counts people went and got: Holmes's test runs, Ehn's stress-test logs, the wait Phillimore asked to have counted. If you don't know how many items are usually in flight in your queue, find out first.

If the answer is zero or one, and every requester waits for its result, a slot and a condition variable are the queue. The request lives on the requester's stack, so nothing is allocated and nobody negotiates who frees what, and a counter records the assumption:

```cpp
struct Request {                              // lives on the requester's stack
    std::function<void()> work;
};

class OneSlot {
    std::mutex m_;
    std::condition_variable server_cv_;       // the server waits here for a request
    std::condition_variable requester_cv_;    // requesters wait here: to install, then to finish
    Request* slot_ = nullptr;                 // capacity one
    bool stop_ = false;
    std::uint64_t waited_behind_ = 0;         // requests that found the slot taken
public:
    void submit_and_wait(Request& r) {        // any number of requesters
        std::unique_lock lk(m_);
        if (slot_ != nullptr) ++waited_behind_;
        requester_cv_.wait(lk, [&] { return slot_ == nullptr; });
        slot_ = &r;
        server_cv_.notify_one();
        requester_cv_.wait(lk, [&] { return slot_ != &r; });  // cleared only after the work
    }
    void serve_loop() {                       // exactly one server
        std::unique_lock lk(m_);
        for (;;) {
            server_cv_.wait(lk, [&] { return slot_ != nullptr || stop_; });
            if (slot_ == nullptr) return;     // stopped with nothing pending
            Request* r = slot_;
            lk.unlock();
            r->work();                        // outside the lock; r's owner is still waiting
            lk.lock();
            slot_ = nullptr;                  // "done" and "slot free" in one store
            requester_cv_.notify_all();
        }
    }
    void stop() {                             // call once every requester has returned
        std::lock_guard lk(m_);
        stop_ = true;
        server_cv_.notify_one();
    }
    std::uint64_t waited_behind() {
        std::lock_guard lk(m_);
        return waited_behind_;
    }
};
```

HotSpot uses one monitor for both waits; the sketch gives the server its own condition variable, but the `notify_all` on completion is still Daugherty's cost. The slot also needs requesters that wait, since a fire-and-forget producer would block whenever the slot was full; in HotSpot the 2019 change came first. Both classes pass a stress harness (sixteen requesters, ten thousand requests each, every request checked to run exactly once) under ThreadSanitizer, AddressSanitizer and UBSan.

<!-- AUTHOR: The harness lives at tools/hotspot_series/sketches/two-commits-one-day-the-queue-hotspot-added-and-the-queue-it-deleted.cpp in the blog repo. Decide whether to link it, and whether you want "I ran it" wording once you have run it yourself. -->

If a message only means "there is work", make it a bit in a `std::atomic<std::uint32_t>`, as in the mailbox above.

A consumer that filters or ranks has to see every item, so it scans under its own lock. Producers can still push without that lock, as the handshake queue's do, but every waiter needs a guaranteed wake-up, including the ones whose items the consumer filters out. Leyden's hang was that wake-up going missing.

If anyone reads "empty" as a signal, remove the item after the work is done.

Priorities bring starvation. HotSpot's counter lasted 19 years and went only when VM operations had become rare enough to drop the queue.

Library queues (moodycamel::ConcurrentQueue, boost::lockfree::queue, folly's MPMCQueue) pick the container for you, but none of them will skip items a consumer may not take, re-rank items while they wait, or keep an item visible until its work is done.

## The Standard Queue, With Parts Removed

Every structure here is the standard queue, the `std::mutex`, `std::condition_variable` and `std::deque` of [the first queue post]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), with something taken out: the VM thread's slot has no capacity beyond one and no order among its waiters, the handshake queue gave up its head pointer and its strict order, the compile queue swapped order for a scan, and JFR's post box carries no payload. The two argued in public rested on traffic claims: "rarely >1", "very rare", "99% of the time the queue is empty". Claims like those are what to instrument, because nothing else will tell you when they stop being true.

A lock-free push with a locked pop is the smallest deviation here, and it works when the consumer already holds a lock for some other reason. The handshake queue's consumer held it to claim the target thread, so the hard parts (filtering, freeing, deciding what "empty" means) stayed with one thread at a time.

## Go Count Yours

Before the next post, put a counter on your own queue. In `push`, read the depth before inserting and bump one of five buckets: 0, 1, 2-3, 4-15, 16 or more. Run a day of ordinary load, then the worst load you can produce, and read the histogram before you touch the container. If nearly every push lands in the zero bucket and every caller waits for an answer, you may be maintaining a queue for traffic a slot would carry. If the high buckets fill, go back to Pick Two and decide what happens when they overflow.

---

Sources: HotSpot code is linked at `jdk-27-ga` (815ff4dc) where it is current, and otherwise at the commit where it lived (6230791e9ef, 6d19fe65d1c). The two commits are 6bddeb709d1 and 431338bcb3c; the others are 3d7d54b1457 (fire-and-forget removed), a10a9284c19 (batching removed) and 2bfd708e927 (peek, execute, remove). Versions are the first build tag containing each commit.

No benchmarks in this post: every number is quoted from the linked sources. The C++ sketch is shown in full above, minus its `#include`s and its stress harness.

**Previously:** Pick Two: What Your Queue Does When the Consumer Falls Behind

**Next:** HotSpot for C++ Programmers (coming soon)
