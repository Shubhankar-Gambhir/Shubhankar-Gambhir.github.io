---
title: "HotSpot for C++ Programmers: A Million Lines Without std::mutex"
date: 2026-10-07
categories: [C++, Concurrency]
tags: [hotspot, jvm, openjdk, threads, safepoint, handshake, jit, atomics, concurrency]
description: >-
  The JVM is a C++ program that starts VM, GC and compiler threads to print hello world,
  and it keeps std::mutex, std::atomic and std::thread out of everything that matters.
  A C++ programmer's tour of the HotSpot internals you need before reading its
  producer-consumer queues: the thread cast, safepoints and handshakes, tiered
  compilation, and the VM's own concurrency toolkit.
image:
  path: /assets/img/og/hotspot-for-cpp-programmers-a-million-lines-without-std-mutex.png
  alt: "HotSpot for C++ Programmers: A Million Lines Without std::mutex"
  hero: false
---

HotSpot, the virtual machine at the core of OpenJDK, is roughly a million lines of C++, and it uses neither `std::mutex` nor `std::atomic` nor `std::thread` for anything that matters.

After a few posts benchmarking [my own C++ queues]({% post_url 2026-07-07-a-deep-dive-into-producer-consumer-queues %}), I wanted to see how a real system makes the same trade-offs, and how a large open-source project actually implements them.

Inside HotSpot are about a dozen producer-consumer queues, and this series takes several of them apart. The first is a per-thread log that a Java pointer store writes to while the garbage collector is marking, and [the next post]({% post_url 2026-10-15-hotspot-for-cpp-programmers-the-queue-inside-a-java-pointer-store %}) reads it instruction by instruction. Reading any of them takes some JVM vocabulary first, but no Java: everything we read is C++.

All source links point at the [`jdk-27-ga`](https://github.com/openjdk/jdk/tree/jdk-27-ga) tag of OpenJDK. Where history matters, later posts will link the older tags where the old code lived.

## HotSpot Is a C++ Program

HotSpot's source lives under [`src/hotspot`](https://github.com/openjdk/jdk/tree/jdk-27-ga/src/hotspot), with a little assembly per CPU beside the C++. It contains a bytecode interpreter, two just-in-time compilers, several garbage collectors, a low-overhead event recorder and profiler called Java Flight Recorder (JFR), a logging framework, and a lot of runtime plumbing. When you run `java -jar app.jar`, the `java` launcher loads `libjvm.so` and HotSpot takes it from there.

It is an unusual C++ codebase, and the [HotSpot style guide](https://github.com/openjdk/jdk/blob/jdk-27-ga/doc/hotspot-style.md#c-standard-library) gives the reasons it keeps the standard library out. HotSpot does not use exceptions or the global allocator, which rules out most containers. It has its own threading, which has to cooperate with the VM in ways we will see shortly. And `<atomic>` is [banned outright](https://github.com/openjdk/jdk/blob/jdk-27-ga/doc/hotspot-style.md#atomic) because HotSpot's atomics "must have semantics which are consistent with those provided by the JDK's compilers for Java", including a "conservative" ordering that may be stronger than sequentially consistent. The C++ runtime and the Java code it compiles share one memory model, so the C++ side cannot leave the details to whichever C++ compiler built it. It also generates much of its hottest code at run time, with an in-process assembler, which means some of the code this series cares most about never exists as a C++ function you could put into Compiler Explorer.

The source layout you will see linked throughout:

| Directory | What lives there |
|---|---|
| `share/runtime/` | Threads, locks, safepoints, the VM thread |
| `share/gc/shared/`, `share/gc/g1/`, ... | Garbage collectors, one directory each |
| `share/compiler/` | The compile queue and the policy that feeds it |
| `share/jfr/`, `share/logging/` | Java Flight Recorder and the logging framework (`-Xlog`) |
| `share/utilities/` | Generic data structures, including some of the queues |
| `cpu/x86/`, `cpu/aarch64/`, ... | Per-architecture assembler and code generators |

## The Thread Cast

A running JVM has far more threads than the program it is running. Even a Java "hello world" on JDK 27 brings up a whole set of them beside the one that runs `main`: the VM thread, garbage collector workers, JIT compiler threads and a service thread, plus more once you switch on asynchronous logging, Java Flight Recorder or a profiler. Almost every queue in this series sits between two of them, so it is worth meeting the cast.

HotSpot draws one line through all of them, in the class hierarchy comment at the top of [`thread.hpp`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/runtime/thread.hpp#L71):

```text
Thread
  JavaThread          # can run Java code, takes part in safepoints
    CompilerThread, ServiceThread, ...
  NonJavaThread       # never runs Java code
    NamedThread
      VMThread
      ConcurrentGCThread
      WorkerThread
    WatcherThread, AsyncLogWriter, ...
```

The ones that matter for queues:

| Thread | Kind | What it does | Shows up in |
|---|---|---|---|
| Application threads | `JavaThread` | Run the user's Java code. GC literature calls them mutators, because from the collector's point of view their job is to mutate the object graph. | every post |
| VMThread | `NonJavaThread` | A single thread that runs "VM operations": anything that needs the whole JVM stopped. | Two Commits, One Day |
| Compiler threads | `JavaThread` | Pull methods off the compile queue and JIT them. | Two Commits, One Day |
| GC worker threads | `NonJavaThread` | Do the collector's parallel work; they share it by stealing from each other. | Who Frees the Node? |
| Concurrent GC threads | `NonJavaThread` | Run collector phases alongside the application, such as concurrent marking. | [the next post]({% post_url 2026-10-15-hotspot-for-cpp-programmers-the-queue-inside-a-java-pointer-store %}); A Lock-Free Queue With One User |
| Service thread | `JavaThread` | A catch-all housekeeper that drains several event queues. | not covered in this series |
| JFR recorder, async log writer | mixed | Drain diagnostic data that other threads produce. | [Pick Two]({% post_url 2026-11-12-pick-two-what-your-queue-does-when-the-consumer-falls-behind %}) (the async log writer) |

The `JavaThread` / `NonJavaThread` split decides who has to cooperate when the VM needs to stop the world, and that turns out to constrain how HotSpot's locks and queues are built.

## Stopping Threads: Safepoints and Handshakes

Some operations need every application thread frozen at a known point: certain GC phases, deoptimizing compiled code, redefining a class. A C++ program would usually reach for a lock that everyone takes. HotSpot cannot afford that on every operation in the hot path, so it does the reverse: application threads *poll*, cheaply and often, and the VM flips a switch when it needs them to stop.

A point in the code where a thread is allowed to stop is a safepoint. Stopping every Java thread at one is what people mean by a stop-the-world pause. The poll itself is tiny. Here is the one that C2, HotSpot's optimizing just-in-time compiler (more on it [under tiered compilation](#tiered-compilation)), emitted at the end of a method in the listing that [the next post]({% post_url 2026-10-15-hotspot-for-cpp-programmers-the-queue-inside-a-java-pointer-store %}) dissects:

```nasm
        cmp     0x28(%r15), %rsp    ; compare the stack pointer with this thread's polling word
        ja      .Lslow              ; armed: leave the fast path and stop for the VM
        ret                         ; not armed: return normally
```

`%r15` holds the current `JavaThread*` in compiled code, so `0x28(%r15)` is a field of the thread object, its *polling word*. When the VM wants this thread to stop, it writes a value there that makes the compare fail, and the thread diverts into the runtime and parks. (The same word doubles as a stack watermark used by some collectors, which is why a poll on the return path compares it against `%rsp`; polls elsewhere test a single bit instead. [`MacroAssembler::safepoint_poll`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/cpu/x86/macroAssembler_x86.cpp#L2500) emits both forms.)

Because the polling word is per-thread, the VM can also arm *one* thread instead of all of them. That is a handshake: run a small piece of work against a single thread while it is stopped, and let every other thread carry on. The handshake requests aimed at a thread wait in a queue of their own.

The poll only works if the thread is running code that polls. A thread blocked in a lock, or sitting in native code, is not polling, so HotSpot tracks a state per thread ([`JavaThreadState`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/utilities/globalDefinitions.hpp#L1024)): `_thread_in_Java`, `_thread_in_vm`, `_thread_in_native`, `_thread_blocked`. A thread in native code or blocked already counts as stopped for safepoint purposes, as long as it checks in before it touches the heap again. This is why HotSpot's mutexes cannot be a bare `std::mutex`. Underneath there is an ordinary platform lock, but a `JavaThread` that has to wait for it first switches its state to "blocked" ([`Mutex::lock_contended`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/runtime/mutex.cpp#L102)), or a stop-the-world pause would wait forever for a thread that is waiting for the lock.

## Tiered Compilation

HotSpot starts every method in its bytecode interpreter, which is slow but needs no warm-up. As a method runs, the VM counts invocations and loop iterations. When a counter crosses a threshold the method is queued for compilation, first by C1, a fast compiler that produces decent code and keeps profiling, and later, if the method stays hot, by C2, a slow, heavily optimizing compiler. That progression is tiered compilation. (The default thresholds include [`Tier3InvocationThreshold`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/compiler/compiler_globals.hpp#L175) at 200 invocations for C1 and [`Tier4InvocationThreshold`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/compiler/compiler_globals.hpp#L193) at 5000 for C2, with several other counters in play.)

For this series, what matters is how compile tasks get from one thread to another. Any Java thread can produce a compile task, just by being the one that happens to cross a threshold. Dedicated compiler threads consume them. There are two queues, one per compiler ([`compileBroker.hpp`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/compiler/compileBroker.hpp#L200)), and the consumer does not take the oldest task: it scans the whole queue and picks the method that is getting hotter fastest ([`CompilationPolicy::select_task`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/compiler/compilationPolicy.cpp#L691)). A consumer that needs to see every element before it picks one is a strong hint about which synchronization strategy won, and Two Commits, One Day, later in this series, is where we find out.

## HotSpot's Concurrency Toolkit

The last piece of vocabulary is HotSpot's own versions of the primitives you already know.

| HotSpot | Closest C++ equivalent |
|---|---|
| [`Mutex`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/runtime/mutex.hpp#L168) | `std::mutex` |
| [`Monitor`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/runtime/mutex.hpp#L227) | `std::mutex` + `std::condition_variable` |
| [`Atomic<T>`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/runtime/atomic.hpp#L34) | `std::atomic<T>` |
| `AtomicAccess::` | `std::atomic_ref` |
| [`GlobalCounter`](https://github.com/openjdk/jdk/blob/jdk-27-ga/src/hotspot/share/utilities/globalCounter.hpp#L34) | RCU, epoch-based reclamation |
| Thread-local GC data | `thread_local` |

Every HotSpot `Mutex` has a rank, and debug builds assert that a thread always acquires locks in rank order, so a lock-order deadlock fails an assertion the first time the order is violated instead of hanging the first time two threads actually race. Every locker also says whether the VM may reach a safepoint while it waits (`_safepoint_check_flag` or `_no_safepoint_check_flag`), which is the cooperation from the safepoint section made explicit. A `Monitor` is a `Mutex` with `wait` and `notify` built in, and most of the blocking queues in this series turn out to be a `Monitor` guarding a linked list.

`GlobalCounter` is the one with no standard equivalent. Readers bracket their access with a cheap critical section, a store to thread-local memory and a fence, and a writer that wants to free memory calls `write_synchronize`, which waits until every reader that might still hold a pointer to it has left. This is how HotSpot's lock-free stacks avoid ABA without [tagged pointers]({% post_url 2026-07-15-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}#making-the-pointer-change). The thread-local GC data is less exotic than it sounds: a fixed block of bytes inside every `Thread` object that the collector owns, reached through `%r15` in compiled code, and the home of the SATB log that [the next post]({% post_url 2026-10-15-hotspot-for-cpp-programmers-the-queue-inside-a-java-pointer-store %}) takes apart.

The atomics need a warning, because the history sections in this series will link code from before and after a recent change. `Atomic<T>` is a class template added in JDK 26. Before that, HotSpot spelled every atomic operation as a static function on a plain `volatile` field, through a class that was itself called `Atomic`, and JDK 26 renamed that class to `AtomicAccess`. So `Atomic::cmpxchg(&_top, old, new)` in a JDK 21 or 25 link and `_top.compare_exchange(old, new)` in a JDK 27 link are the same operation.

---

Source: OpenJDK [`jdk-27-ga`](https://github.com/openjdk/jdk/tree/jdk-27-ga) (`815ff4dc327f`). The safepoint poll comes from the barrier capture whose program and flags are in the footer of [the next post]({% post_url 2026-10-15-hotspot-for-cpp-programmers-the-queue-inside-a-java-pointer-store %}).

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz, Linux 5.15.

**Next:** [HotSpot for C++ Programmers: The Queue Inside a Java Pointer Store]({% post_url 2026-10-15-hotspot-for-cpp-programmers-the-queue-inside-a-java-pointer-store %})
