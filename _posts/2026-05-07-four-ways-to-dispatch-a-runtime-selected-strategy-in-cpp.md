---
title: "Four Ways to Dispatch a Runtime-Selected Strategy in C++"
date: 2026-05-07
categories: [C++, Performance]
tags: [dispatch, virtual, crtp, variant, function-pointer, benchmarks, assembly, openjdk]
description: >-
  Head-to-head comparison of virtual dispatch, function pointers, std::variant,
  and decoupled CRTP for runtime plugin dispatch — with benchmarks, assembly,
  and a decision framework.
---

You're building a system with pluggable strategies. The user picks one at startup — a config flag, a command-line argument — and every call goes through that strategy. The call runs millions of times per second. How do you dispatch it?

This post compares four approaches to this problem using the same domain, the same API, and the same three plugins. We'll look at the generated assembly, measure dispatch overhead, and build a decision framework for picking the right one.

## The Scenario

Consider a garbage collector barrier system (borrowed from OpenJDK). The user types `-XX:+UseG1GC` at startup. Every heap write goes through a *barrier set* — `store(addr, value)` — that runs millions of times per second. There are three GC algorithms, each with different barrier logic:

- **Epsilon** — does nothing (no-op barrier)
- **Serial** — adds a post-barrier (card marking)
- **G1** — adds a pre-barrier (SATB snapshot) *and* a post-barrier (card marking)

We want three things:

1. **Runtime flexibility** — the user picks the strategy at startup
2. **Minimal dispatch overhead** — every nanosecond counts at 100M calls/sec
3. **Composability** — G1 needs pre + post barriers, Serial needs only post. Can we compose these instead of duplicating?

## Approach 1: Virtual Dispatch

The most familiar approach — an abstract base class with virtual methods:

```cpp
class BarrierSet {
public:
    virtual ~BarrierSet() = default;
    virtual void store(int* addr, int value) = 0;
};

class G1BarrierSet : public BarrierSet {
public:
    void store(int* addr, int value) override {
        // pre-barrier: SATB snapshot
        printf("  [pre-barrier] SATB snapshot: old=%d\n", *addr);
        // raw store
        *addr = value;
        // post-barrier: card mark
        printf("  [post-barrier] card mark @ %p\n", addr);
    }
};

auto bs = BarrierSet::create(argv[1]);  // "g1", "serial", "epsilon"
bs->store(heap, 42);
```

Clean and familiar. But what does the compiler generate?

```nasm
movq  (%rdi), %rax           ; LOAD 1: vptr from object
call  *16(%rax)              ; LOAD 2: vtable entry → indirect call
```

Two dependent memory loads before the call. The `this` pointer occupies `%rdi`, and the compiler cannot inline across the virtual boundary. Each GC also duplicates the full barrier logic — G1 and Serial both contain the raw store and card marking code independently.

## Approach 2: Function Pointer

Strip away the class hierarchy entirely:

```cpp
void epsilon_store(int* addr, int value) {
    *addr = value;
}

void g1_store(int* addr, int value) {
    printf("  [pre-barrier] SATB snapshot: old=%d\n", *addr);
    *addr = value;
    printf("  [post-barrier] card mark @ %p\n", addr);
}

using StoreFn = void(*)(int*, int);
StoreFn store = resolve(argv[1]);  // returns the right function pointer
store(heap, 42);
```

No class hierarchy. No `this` pointer. Just a function address. The assembly:

```nasm
call  *%r12                  ; indirect call through register
```

One indirect call. The pointer lives in a callee-saved register, so there's no memory load at all — just the indirect branch. Arguments go directly in `%rdi` and `%rsi` without the `this` pointer overhead.

But like virtual dispatch, each function reimplements the full barrier logic. No composition.

## Approach 3: std::variant + std::visit

The modern C++ approach — a closed type set with compiler-generated dispatch:

```cpp
using AnyBarrierSet = std::variant<EpsilonBarrierSet, SerialBarrierSet, G1BarrierSet>;

auto bs = create(argv[1]);
std::visit([&](auto& b) { b.store(addr, value); }, bs);
```

No base class. No vtable. The compiler sees all types. This should be fast... right?

Here's what libstdc++ (GCC 11) actually generates:

```nasm
movq  %rbp, 16(%rsp)        ; STORE 1: spill to lambda capture
movq  %r13, 24(%rsp)        ; STORE 2: spill to lambda capture
movzbl 7(%rsp), %eax        ; load variant index
call  *(%r14,%rax,8)        ; indexed indirect call into _S_vtable
```

libstdc++ generates **its own function pointer table** (`_S_vtable`) — essentially a vtable. On top of that, there are two stack stores per call to build the lambda capture struct that the visited function reads back.

It's a vtable with extra steps. More overhead than virtual dispatch, not less.

> **Caveat:** This is a libstdc++ implementation quality issue. libc++ or MSVC may generate different code. Always measure with your toolchain.

The extensibility story is also worse: adding a new GC means modifying the `variant` typedef, which touches every file that uses it. The type set is closed by definition.

## Approach 4: Decoupled CRTP + Lazy Resolution

This is what OpenJDK actually uses. Three patterns work together:

### The AccessBarrier Chain (Compile-Time Decoration)

Each layer adds one barrier concern and delegates to its parent:

```cpp
// Layer 0: raw write
class BarrierSet {
    template <typename BarrierSetT>
    class AccessBarrier {
        static void store(int* addr, int value) { *addr = value; }
    };
};

// Layer 1: adds post-barrier (card marking)
class ModRefBarrierSet : public BarrierSet {
    template <typename BarrierSetT>
    class AccessBarrier : public BarrierSet::AccessBarrier<BarrierSetT> {
        static void store(int* addr, int value) {
            Raw::store(addr, value);           // delegate to parent
            bs->write_ref_field_post(addr);    // card mark
        }
    };
};

// Layer 2: adds pre-barrier (SATB)
class G1BarrierSet : public ModRefBarrierSet {
    template <typename BarrierSetT>
    class AccessBarrier : public ModRefBarrierSet::AccessBarrier<BarrierSetT> {
        static void store(int* addr, int value) {
            bs->write_ref_field_pre(addr);     // SATB snapshot
            ModRef::store(addr, value);        // delegate (raw + post)
        }
    };
};
```

Each GC only adds **its own concern**. No duplication. The compiler flattens the template chain to straight-line code.

### Lazy Resolution (Resolve Once, Dispatch Forever)

The function pointer starts at `&store_init`. The first call resolves the runtime choice and patches the pointer:

```cpp
struct RuntimeDispatch {
    using StoreFn = void(*)(int*, int);
    static StoreFn _store_func;   // starts at &store_init

    static void store_init(int* addr, int value) {
        StoreFn func;
        switch (BarrierSet::barrier_set()->kind()) {
            case G1:      func = &G1BarrierSet::AccessBarrier<>::store;      break;
            case Serial:  func = &SerialBarrierSet::AccessBarrier<>::store;  break;
            case Epsilon: func = &EpsilonBarrierSet::AccessBarrier<>::store; break;
        }
        _store_func = func;    // PATCH — future calls skip the switch
        func(addr, value);     // first call goes through
    }

    static void store(int* addr, int value) {
        _store_func(addr, value);   // after first call: single indirect call
    }
};
```

After the first call, the assembly is identical to the function pointer approach:

```nasm
call  *_store_func(%rip)     ; indirect call through global
```

Same dispatch cost as a function pointer, but the **target function is composed** — each barrier concern is layered, not duplicated.

## Benchmarks

All measurements on an Intel Xeon Gold 6130 @ 2.10 GHz, 100M iterations with G1 barriers, compiled with GCC 11 at `-O2 -march=skylake-avx512`.

| Approach | ns/call | Overhead vs direct |
|---|---|---|
| Direct call (baseline) | 1.48 ns | — |
| Decoupled CRTP | 2.42 ns | +0.94 ns |
| Function pointer | 2.43 ns | +0.95 ns |
| Virtual dispatch | 2.90 ns | +1.42 ns |
| std::variant + std::visit | 3.71 ns | +2.23 ns |

CRTP and function pointer are tied. Virtual dispatch costs about 0.5 ns more (the vptr→vtable dependency chain). Variant is slowest due to the lambda capture spilling and `_S_vtable` indirection.

### Binary Size

Text section sizes (dynamically linked, `-O2`):

| Approach | Text section | Relative |
|---|---|---|
| Function pointer | 4,727 bytes | baseline |
| Virtual dispatch | 6,776 bytes | +43% |
| Decoupled CRTP | 7,885 bytes | **+67%** |

CRTP is largest because each composed `AccessBarrier` chain is a separate template instantiation. That's the cost of composability: each unique barrier composition gets its own generated code.

## The Honest Truth

The dispatch overhead differences are **small** — 0.9 to 2.2 ns. At 100M calls/sec, the worst approach (variant) costs ~220 ms of overhead per second. The best (CRTP/fnptr) costs ~95 ms.

The **real** differences are:

- **Composability** — only CRTP lets you layer barrier aspects without duplication
- **Extensibility** — variant requires modifying the type list; the others are open
- **Code organization** — CRTP decouples the base from concrete implementations
- **Familiarity** — virtual dispatch is universal knowledge

## Comparison Matrix

| | Virtual | FnPtr | variant | Decoupled CRTP |
|---|---|---|---|---|
| **Dispatch overhead** | +1.4 ns | +0.9 ns | +2.2 ns | +0.9 ns |
| **Binary size (text)** | 6.8 KB | 4.7 KB | — | 7.9 KB |
| **Compile time** | 0.38s | 0.25s | 0.32s | 0.29s |
| **Lines of code** | 90 | 65 | 76 | 346 |
| **Extensibility** | Open | Open | Closed | Open |
| **Decoupling** | Partial | Poor | Poor | Excellent |
| **Composition** | Duplicated | Duplicated | Duplicated | Layered |
| **Error messages** | Clear | Clear | Moderate | Poor |
| **Debuggability** | Excellent | Good | Moderate | Moderate |
| **Learning curve** | None | None | Low | Steep |

## Decision Framework

```
Need runtime plugin dispatch?
│
├── Type set closed & small?
│   └── std::variant
│       (simplest API, but measure std::visit with YOUR stdlib)
│
├── Simple dispatch, no composition needed?
│   └── Function pointer
│       (lightest weight, zero ceremony)
│
├── Need OOP inheritance & familiar patterns?
│   └── Virtual dispatch
│       (everyone knows it, ~0.5 ns extra is often fine)
│
└── Need composable behaviors + open extensibility?
    └── Decoupled CRTP + Lazy Resolution
        (more code, but behaviors compose without duplication)
```

## When Complexity Pays Off

The decoupled CRTP approach is 5x more code and has the steepest learning curve. It's worth it when:

- You have many plugins with shared cross-cutting concerns (pre/post barriers, logging, auth)
- Plugins are added by different teams who shouldn't touch the base class
- The base must compile independently (large codebase, separate build units)
- You need to compose behaviors without duplicating logic across every plugin

It's **not** worth it when you have fewer than five plugins with no shared behavior, one team owns all the code, or simplicity and onboarding speed matter more than architecture.

OpenJDK has six GCs, each with combinations of pre/post barriers, maintained by different teams, in a codebase with 1M+ lines. The complexity pays for itself.

## Key Takeaways

1. **All four work.** Pick based on constraints, not microbenchmark deltas.
2. **`std::variant + std::visit` is NOT always faster than virtual** — check your stdlib implementation.
3. **CRTP's value is composability**, not raw dispatch speed. Same overhead as a function pointer, but barriers compose via template inheritance.
4. **The right question isn't "which is fastest?"** It's: do I need composition? decoupling? extensibility? Then pick the simplest approach that satisfies those.

---

*The benchmark code, all four implementations, and Compiler Explorer links are available in the [companion repository](https://github.com/Shubhankar-Gambhir). Measured on Intel Xeon Gold 6130 @ 2.10 GHz, GCC 11, libstdc++, `-O2 -march=skylake-avx512`.*
