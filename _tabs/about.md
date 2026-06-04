---
# the default layout is 'page'
icon: fas fa-info-circle
order: 4
---

I'm a software engineer at [Azul Systems](https://www.azul.com/), working on the Zing JVM (a HotSpot fork with the C4 garbage collector, Falcon compiler, and CRaC checkpoint/restore). I spend most of my time in C++ and care about what the compiler generates, what the CPU does with it, and how large systems make design trade-offs between flexibility and performance.

## Current Series

I'm writing a [series on C++ dispatch mechanisms](/categories/performance/): virtual dispatch, function pointers, `std::variant`, and compile-time polymorphism patterns extracted from OpenJDK. Each post includes benchmarks, generated assembly, and `perf stat` data. The series covers how these mechanisms behave under both monomorphic and polymorphic workloads, and how standard library implementation choices can change the results more than the algorithm.

## What I Write About

- **C++ dispatch and polymorphism**: how different dispatch mechanisms perform at the assembly level, and when the abstractions stop being zero-cost
- **Systems design trade-offs**: when complexity earns its keep and when simpler is faster
- **Patterns from production codebases**: reusable C++ techniques borrowed from OpenJDK HotSpot and similar large-scale systems

## Selected Work

- **Breaking PCB-Chain: A Side Channel Assisted Attack on IoT-Friendly Blockchain Mining**, S. Gambhir, V. Mishra, U. Chatterjee et al., Springer, 2025
- **Bitcoin Core**: test infrastructure improvements ([#23052](https://github.com/bitcoin/bitcoin/pull/23052), [#22641](https://github.com/bitcoin/bitcoin/pull/22641)), merged 2021

## Elsewhere

- [GitHub](https://github.com/Shubhankar-Gambhir)
- [LinkedIn](https://www.linkedin.com/in/shubhankar-gambhir-a5b89a1b5/)
