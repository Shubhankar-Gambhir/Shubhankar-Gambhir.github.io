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

I gave ["*Compile-Time Polymorphism for Runtime-Flexible Systems: Lessons from OpenJDK*"](https://cppcon2026.sched.com/event/2RT5T/compile-time-polymorphism-for-runtime-flexible-systems-lessons-from-openjdk) in the Software Design track: how to keep inheritance-style extensibility for a runtime-selected strategy without paying for a vtable, built up incrementally against OpenJDK's garbage collection barriers, where every GC algorithm composes a different set of memory-access barriers on one of the hottest paths in the JVM.

Thirty to forty people showed up, mostly senior engineers, and the session went the way you hope a talk goes: the room stayed for questions after the slides ended, and the questions went deep, past the material I'd prepared, into type erasure and the other ways you can implement an interface without a vtable. Nobody in the room needed to know what a JVM was for the talk to land, which was the whole bet the abstract made.

![Session board showing the talk in progress](/assets/img/a-week-in-aurora-cppcon-2026-talk-screen.jpg)
_Mid-talk, Homestead 3/4._

## The Workshop

I spent two days in [Fedor Pikus's High-Performance Concurrency](https://cppcon.org/class-2026-high-perf-concurrency/) workshop before the main conference started: branchless programming, branch prediction, TLB behavior, and how much copying disappears once you actually use move semantics instead of writing code that happens to compile with them.

The part that stuck with me was the instructor's insistence that undefined behavior sometimes *improves* measured performance, not just that it fails to punish you. That is an uncomfortable thing to say out loud in a room full of people who have spent a week hearing why UB is dangerous, and the workshop did not back away from it: a UB-reliant optimization can beat the well-defined alternative on a given compiler and target, right up until it doesn't, on the next compiler release or the next architecture. The point wasn't "UB is fine." It was that the danger and the performance win come from the same source, the compiler assuming something you didn't actually guarantee, and pretending otherwise is worse than just knowing it.

## Talks Worth Remembering

CppCon runs six tracks at once, so anyone's "best of" list is really a "what I happened to be in the room for" list. Four more stuck, on top of the two keynotes above, out of a schedule with many more sessions than any one person could sit through.

1. ["*Concurrency for Modern CPUs: Lock-Free or Lock-Based?*"](https://cppcon2026.sched.com/event/2RT8G/concurrency-for-modern-cpus-lock-free-or-lock-based) by Fedor Pikus

   Pikus was back on Friday after the workshop, and his answer depends on contention. Under heavy contention a well-written spinlock beats a CAS loop, because systematic backoff makes the losing threads wait their turn, so the cache line changes owners in batches instead of bouncing on every failed retry. Under light contention it flips. Hardware counters show that even an uncontended spinlock isn't free, since the synchronization it imposes gets in the way of the out-of-order pipeline, and a single XADD or CAS doesn't. His queue takes both results seriously: a dual-domain MPMC design that separates the contended path from the uncontended one and lets each run on whichever mechanism wins there, benchmarked on Intel, ARM server parts (Graviton and Grace), and an Apple M3.

   This one landed close to home. In [my own lock-free pool benchmarks]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}), the CAS-based free list lost to the mutex-guarded one on the two-socket Xeon in every mix except fan-out, and my explanation was that a CAS loop has no way to stand down while a contended mutex parks the loser in the kernel. Pikus's spinlock never sleeps and still beats the CAS loop, which tells me sleeping was only one way to stand down; backoff is another. He also called "ARM vs x86" the wrong axis entirely, and that's where my results ended up too: the same CAS loop won all nine mixes on a single-node Neoverse-N1, and the explanation I landed on was the Xeon's two sockets, not its instruction set. Lock-free still has a home in code that needs real progress guarantees, like deadlock freedom or signal-handler safety. In his words, it "is not dead. It has simply relocated."

2. ["*Are You Smarter Than A Branch Predictor?*"](https://cppcon2026.sched.com/event/2RT7J/are-you-smarter-than-a-branch-predictor) by Michelle D'Souza

   This one made me want to go back and re-check every `[[likely]]` I've ever written. Modern branch predictors are good enough now that manual branchless rewrites, or hint attributes added on instinct, can make things worse rather than better, unless they're guided by actual profiling data and an understanding of how the compiler is laying out the resulting code. The predictor usually already knows what you're about to tell it.

3. ["*Processor Design and C++ Memory Models*"](https://cppcon2026.sched.com/event/2RT4z/processor-design-and-c++-memory-models) by Ofek Shilon

   Shilon took the acquire and release semantics you write into an atomic and mapped them straight onto the hardware that executes them: store buffers, invalidation queues, the cache-coherence traffic a fence actually generates. Knowing that `memory_order_acquire` is cheaper than `memory_order_seq_cst` on x86 is one thing; watching exactly which piece of silicon that cost comes from, and why the answer changes on a different microarchitecture, is another. This was the talk that supplied the mental model instead of the mnemonic.

4. ["*Ensuring Code Quality in the Age of AI: More Code, Less Engineering*"](https://cppcon2026.sched.com/event/2RT4q/ensuring-code-quality-in-the-age-of-ai-more-code-less-engineering) by Peter Muldoon

   AI-assisted development hasn't removed the need for engineering judgment, Muldoon argued; it has just moved where the bottleneck sits. Code that used to be slow to write is now slow to review properly, and a reviewer who rubber-stamps a large AI-generated pull request has traded a code-writing problem for a code-review problem. His defense was mostly procedural: tighter PR documentation, spreading review responsibility across a team instead of concentrating it in whoever's fastest, checklists that don't depend on a reviewer's memory. None of it was exotic, which was the point. The tools changed; the discipline required to ship good software didn't.

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
