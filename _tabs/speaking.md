---
layout: page
icon: fas fa-microphone
order: 3
title: Speaking
---

## Upcoming Talks

### Compile-Time Polymorphism for Runtime-Flexible Systems: Lessons from OpenJDK
**CppCon 2026** &#124; Tuesday, September 15, 2026, 15:15-16:15 MDT &#124; Gaylord Rockies, Aurora, Colorado &#124; [Session](https://cppcon2026.sched.com/event/2RT5T/compile-time-polymorphism-for-runtime-flexible-systems-lessons-from-openjdk)

Your system picks a strategy at runtime -- a config flag, a command-line argument -- and the hot path runs millions of times per second. Virtual dispatch charges you on every call; templates give you the performance but lock the choice in at compile time. This session builds a layered architecture of compile-time patterns that keeps inheritance-style extensibility without paying for a vtable, developing the techniques incrementally against OpenJDK's garbage collection barriers, where each GC algorithm composes a different set of barriers on one of the hottest paths in the JVM. Along the way we weigh the approach against `std::variant`, function pointers, and plain virtual dispatch. No JVM knowledge required.

## Past Talks

### Zero-Cost Abstractions in Large Systems: Lessons from OpenJDK's Barrier Refactoring
**C++ Online 2026** &#124; [Video](https://www.youtube.com/watch?v=4aMaSaFW5Qo) &#124; [Session](https://cpponline.uk/session/2026/zero-cost-abstractions-in-large-systems/) &#124; [Slides (PDF)](https://cpponline.uk/wp-content/uploads/2026/03/Zero-Cost-Abstractions.pdf)

How large-scale C++ systems balance performance with design flexibility. Using OpenJDK's memory-access barriers as a case study, this talk demonstrates how a declarative, template-driven architecture can maintain runtime adaptability while eliminating abstraction overhead, combining runtime pluggability with compile-time composition across demanding C++ domains.

## Topics I Speak About

- **Compile-time polymorphism** -- CRTP, template metaprogramming, and the patterns that let production systems achieve runtime flexibility without virtual dispatch overhead
- **Runtime dispatch mechanisms** -- head-to-head comparisons of virtual dispatch, function pointers, `std::variant`, and template-based approaches with real benchmarks and assembly analysis
- **JVM internals for C++ developers** -- how a 1M+ line C++ codebase (OpenJDK HotSpot) solves real engineering problems with patterns you can steal
- **Systems performance** -- memory tracking, logging frameworks, and the design decisions behind production infrastructure

## Get In Touch

Interested in having me speak at your conference or meetup? Reach out via [LinkedIn](https://www.linkedin.com/in/shubhankar-gambhir-a5b89a1b5/) or [GitHub](https://github.com/Shubhankar-Gambhir).
