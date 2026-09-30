---
title: "A Week in Aurora: CppCon 2026"
date: 2026-09-29
categories: [C++, Conferences]
tags: [cppcon, conference, concurrency, branch-prediction, c++26, memory-safety, openjdk, ai-assisted-development]
description: >-
  A recap of CppCon 2026 at the Gaylord Rockies: a talk on OpenJDK's compile-time
  dispatch patterns, two keynotes on C++ safety and the C++26 object model, and
  the sessions on hardware performance and AI-assisted code quality that stuck
  past the flight home.
image:
  path: /assets/img/og/a-week-in-aurora-cppcon-2026.png
  alt: "A Week in Aurora: CppCon 2026"
  hero: false
---

Last year my company sent me to C++ on Sea (now ACCU on Sea) as an attendee, and I came home thinking I should try submitting a talk of my own somewhere. Luckily, CppCon said yes. I spent the week of September 15th in Aurora, Colorado for CppCon 2026, out of the Gaylord Rockies, there as a speaker thanks to the conference's sponsors covering my travel and lodging, and spent two days before the show in [Fedor Pikus's High-Performance Concurrency](https://cppcon.org/class-2026-high-perf-concurrency/) workshop, thanks to Azul picking up the fee. This is not the deep technical dive most posts here are. It is a recap: the keynotes, what I presented, what I sat through, and a few things that stuck past the flight home.

![Outside the Gaylord Rockies during CppCon 2026](/assets/img/a-week-in-aurora-cppcon-2026-venue.jpg)
_Outside the Gaylord Rockies._

## Keynotes

CppCon ran five keynotes this year. Two of them are worth a section of their own.

["*Profiles for Simplicity and Guarantees*"](https://cppcon2026.sched.com/event/2RT2n/profiles-for-simplicity-and-guarantees) laid out where the C++ safety story is actually heading: opt-in static constraints a codebase can adopt incrementally, not a new dialect and not a Rust-style rewrite. You declare which profile you're building to, and the compiler enforces the memory and type safety guarantees that profile promises from there. It's a pragmatic answer to a question that's been hanging over the language for a few years now, how to close the safety gap without asking every existing codebase to start over.

["*The Address Is Not The Place: Object Residency in C++26*"](https://cppcon2026.sched.com/event/2RT2t/the-address-is-not-the-place-object-residency-in-c++26) covered proposed C++26 object model changes that decouple where memory is allocated from where an object lives, aimed at relocatable objects and better interop with dynamic memory and garbage-collected runtimes. Coming at this from the OpenJDK side, where the collector moves objects out from under running threads as a matter of routine, it's a strange feeling to watch C++ start reasoning carefully about a problem the JVM has had opinions about for two decades. Worth tracking if you work anywhere near an allocator.

## My Talk

My talk was on Tuesday, in the Software Design track: ["*Compile-Time Polymorphism for Runtime-Flexible Systems: Lessons from OpenJDK*"](https://cppcon2026.sched.com/event/2RT5T/compile-time-polymorphism-for-runtime-flexible-systems-lessons-from-openjdk). Say your program picks a strategy at runtime, from a config flag or a command-line option, and then runs it on a hot path millions of times a second. Virtual dispatch charges you on every call. Templates are fast, but they lock the choice in at compile time. The talk builds up a way to get both, one step at a time, with OpenJDK's GC barriers as the running example; every collector needs its own mix of barriers, and they sit on one of the hottest paths in the JVM. If you've read the [dispatch series]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}) here, a lot of it will look familiar.

Somewhere between thirty and forty people came, mostly senior engineers. A lot of them stayed after the slides for questions, which went into type erasure and other ways to implement an interface without a vtable, some of it past what I'd prepared.

![Session board showing the talk in progress](/assets/img/a-week-in-aurora-cppcon-2026-talk-screen.jpg)
_Mid-talk, Homestead 3/4._

## The Workshop

I spent two days in [Fedor Pikus's High-Performance Concurrency](https://cppcon.org/class-2026-high-perf-concurrency/) workshop before the main conference started: branchless programming, branch prediction, TLB behavior, and how much copying disappears once you actually use move semantics instead of writing code that happens to compile with them.

The part that stuck with me was the instructor's insistence that undefined behavior sometimes *improves* measured performance, not just that it fails to punish you. That is an uncomfortable thing to say out loud in a room full of people who have spent a week hearing why UB is dangerous, and the workshop did not back away from it: a UB-reliant optimization can beat the well-defined alternative on a given compiler and target, right up until it doesn't, on the next compiler release or the next architecture. The point wasn't "UB is fine." It was that the danger and the performance win come from the same source, the compiler assuming something you didn't actually guarantee, and pretending otherwise is worse than just knowing it.

## Talks Worth Remembering

CppCon runs six tracks at once, so whatever you pick, you miss most of the schedule. Besides the keynotes, these are the four talks I'm still thinking about.

1. ["*Concurrency for Modern CPUs: Lock-Free or Lock-Based?*"](https://cppcon2026.sched.com/event/2RT8G/concurrency-for-modern-cpus-lock-free-or-lock-based) by Fedor Pikus

   Pikus was back on Friday after the workshop, and his answer basically came down to contention. When a lot of threads are fighting over the same data, a well-written spinlock beats a CAS loop. The trick is backoff. Losing threads wait their turn instead of retrying right away, so the cache line changes hands in batches instead of bouncing around on every failed attempt. When contention is low, it goes the other way. Even an uncontended spinlock isn't free, because its synchronization gets in the way of the out-of-order pipeline, and he had hardware counters to show it. A single XADD or CAS doesn't have that problem. So his MPMC queue uses both, a lock on the contended path and atomics on the uncontended one, with benchmarks on Intel, ARM servers (Graviton and Grace), and an Apple M3.

   I recognized a lot of this from my own benchmarks. In the [lock-free pool post]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}), the CAS-based free list lost to the mutex version on the two-socket Xeon in every mix except fan-out. My explanation back then was that a CAS loop has no way to stand down, while a contended mutex puts the losing thread to sleep. Pikus's spinlock never sleeps and still beats the CAS loop, so I think sleeping was just one way of standing down. He also said "ARM vs x86" is the wrong way to compare chips, and that matched what I saw. The same CAS loop won all nine mixes on a single-node Neoverse-N1, and I'd put the difference down to the Xeon's two sockets, not its instruction set. Lock-free isn't going away, though. Anything that needs real progress guarantees, like deadlock freedom or signal-handler safety, still needs it. As he put it, lock-free "is not dead. It has simply relocated."

2. ["*Are You Smarter Than A Branch Predictor?*"](https://cppcon2026.sched.com/event/2RT7J/are-you-smarter-than-a-branch-predictor) by Michelle D'Souza

   This one was set up as a game show. Two C++ snippets go up on the screen, the audience votes on which one runs faster, and then you see what the hardware actually did. The snippets came from real production code, and right answers won erasers. It made me want to go back and check every `[[likely]]` I've ever written. Branch predictors are good enough now that a branchless rewrite or a hint added on a hunch can make things slower, unless you've profiled it and looked at the code the compiler actually generated.

3. ["*Processor Design and C++ Memory Models*"](https://cppcon2026.sched.com/event/2RT4z/processor-design-and-c++-memory-models) by Ofek Shilon

   Shilon's abstract called `std::atomic` and `std::memory_order` "utterly opaque abstractions," and then the talk opened them up. He went through cache coherence, store buffers, invalidation queues, and the load-store queue inside the core, and showed what a fence or a read-modify-write actually does to each of them, on x86 and on ARM and RISC-V, which can behave very differently. A lot of memory-ordering explanations eventually fall back on "the processor does weird things." This one didn't, and I came out with an actual picture of the hardware instead of a list of rules about which ordering is cheap.

4. ["*Ensuring Code Quality in the Age of AI: More Code, Less Engineering*"](https://cppcon2026.sched.com/event/2RT4q/ensuring-code-quality-in-the-age-of-ai-more-code-less-engineering) by Peter Muldoon

   Muldoon's point was that AI has made writing code fast, but it hasn't made reviewing it any faster, so the bottleneck just moves to review. A big AI-generated PR that gets approved without a proper read is still a problem; it's just a review problem now. His fixes were practical: better PR descriptions, spreading reviews across the team instead of leaving them to whoever is quickest, and checklists so reviewers don't have to rely on memory. None of it is new advice, but it matters more now that PRs keep getting bigger.

## What Tied It Together

Looking back, it's less a list of talks and more one lesson wearing different costumes. I went into the lock-free talk assuming contention would settle the spinlock-versus-CAS argument for good, and came out learning it only settles it locally, for one chip, at one contention level. The branch predictor talk did the same thing to my intuition about hint attributes. The memory model talk did it to whatever I thought an atomic actually costs. Even the C++ safety story, which I'd expected to land on some Rust-shaped rewrite, turned out to be about layering opt-in constraints onto code that already exists. By the time the AI-quality talk came around, I recognized the pattern immediately: a tool that changes how fast you produce something doesn't change how much judgment the result still needs. Check the assumption instead of repeating it.

The best conversation I had outside a session room was with an engineer at a trading firm, most of whose stack is C++, and it turned into a good half hour on why HFT shops stay wary of the JVM even when they don't run it themselves. The exchanges they trade against often do run on Java, so JVM pause behavior and GC tuning are something they end up reasoning about secondhand, whether or not a line of Java ever ships in their own systems. It's an odd kind of dependency: caring deeply about a runtime you didn't choose and can't tune, because the other side of your order book did.

## Closing

CppCon wrapped with a nice view, a rainbow right over the venue on the last day.

![Rainbow over the Gaylord Rockies on the last day of CppCon 2026](/assets/img/a-week-in-aurora-cppcon-2026-rainbow.jpg)
_The view from the venue, last day._

Slides and a recording for my talk should show up once CppCon finishes processing the backlog; I'll link them from the [Speaking page](/speaking/) when they're up. If you were in the room for the Q&A and want to keep arguing about type erasure, I'm easy to find.

## Rocky Mountain Arsenal

I stuck around an extra day and went for a hike at the Rocky Mountain Arsenal, this sprawling wildlife refuge just outside Denver that used to be a wartime manufacturing site before it got turned into one of the biggest urban nature preserves in the country. We didn't have a car, so the bison drive was off the table, but walking the trails more than made up for it: lake after lake, dead calm, mirroring the sky back at you.

![One of the lakes at Rocky Mountain Arsenal National Wildlife Refuge](/assets/img/a-week-in-aurora-cppcon-2026-rma-lake.jpg)
_One of the refuge's lakes, the day after CppCon ended._

A pretty nice way to wind the trip down before flying home.

---

Related: [Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %})
