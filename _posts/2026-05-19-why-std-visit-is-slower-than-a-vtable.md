---
title: "Why std::visit Is Slower Than a Vtable"
date: 2026-05-19
categories: [C++, Performance]
tags: [dispatch, variant, virtual, assembly, stdlib, libstdc++, libc++, benchmarks]
description: >-
  std::variant is stack-allocated with no vtable, so why is std::visit 28%
  slower than virtual dispatch? We crack open the assembly and the stdlib
  source to find out.
---

If you benchmark dispatching 100M calls through three small strategy objects -- a no-op, one that does a write after the call, and one that does a read before and a write after -- you'd expect `std::variant + std::visit` to be faster than virtual dispatch. The variant is stack-allocated, there's no heap indirection, no vtable pointer, and the compiler can see the entire closed type set. In a [previous post]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}) comparing four dispatch approaches, `std::variant` was actually the *slowest*: 3.72 ns/call versus 2.87 ns for virtual dispatch. That's 28% slower.

Something is wrong with that picture. So where does the overhead come from?

The answer is in `std::visit`, not `std::variant`. The variant itself is fine. It's the dispatch mechanism that pays for features you probably don't need on your hot path. Let's look at what the compiler actually generates.

## Virtual Dispatch: The Known Quantity

Virtual dispatch is the baseline everyone knows. Here's the hot loop from our benchmark ([bench_virtual.cpp](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark), GCC 11.4, `-O2 -march=skylake-avx512`):

```nasm
.L36:
    movq  8(%rsp), %rdi          ; load BarrierSet* from stack
    movq  (%rdi), %rax           ; LOAD 1: vptr from object
    movl  %ebx, %edx             ; value argument
    call  *16(%rax)              ; LOAD 2: vtable[2] -> indirect call
    incq  %rbx
    cmpq  $100000000, %rbx
    jne   .L36
```
[See it on Compiler Explorer ->](https://godbolt.org/z/eYqqGvrK1)

Two dependent memory loads, then a call. The second load (`call *16(%rax)`) cannot start until the first (`movq (%rdi), %rax`) completes, because the vtable address depends on the vptr. That's the cost: two serial cache accesses per call.

But there's a reason this works well in practice. The vptr doesn't change between calls. The branch predictor learns the target quickly and predicts it correctly on every subsequent iteration. For a monomorphic call site (one concrete type at runtime), virtual dispatch is essentially free after warmup -- the CPU speculatively executes through the indirect call without stalling.

## What `std::visit` Actually Generates

Now here's the hot loop for `std::visit` on the same benchmark ([bench_variant.cpp](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark), same compiler, same flags):

```nasm
.L31:
    movq  %rbp, 16(%rsp)         ; STORE 1: spill heap pointer to lambda capture
    movq  %r13, 24(%rsp)         ; STORE 2: spill loop counter to lambda capture
    movzbl 7(%rsp), %eax         ; load variant discriminant (which type?)
    movq  %r12, %rsi             ; pass variant address
    movq  %rbx, %rdi             ; pass lambda capture address
    call  *(%r14,%rax,8)         ; indexed indirect call into _S_vtable
    movq  8(%rsp), %rax
    incq  %rax
    movq  %rax, 8(%rsp)
    cmpq  $99999999, %rax
    jle   .L31
```
[See it on Compiler Explorer ->](https://godbolt.org/z/f1T14Pdbx)

There's more happening here. Before the actual dispatch:

1. **Two stack stores** (`movq %rbp, 16(%rsp)` and `movq %r13, 24(%rsp)`) build the lambda capture struct. The visitor lambda `[&](auto& b) { b.store(...); }` captures local variables by reference, and `std::visit` passes the visitor as an argument to a generated function. Those captured references need to live at an address the callee can read.

2. **A discriminant load** (`movzbl 7(%rsp), %eax`) reads the variant's index byte -- a one-byte tag that identifies which alternative is currently stored.

3. **An indexed indirect call** (`call *(%r14,%rax,8)`) uses the discriminant to index into a function pointer table at `%r14`. This is `_S_vtable`, a compile-time generated array of function pointers, one per alternative.

The irony: libstdc++ builds **its own vtable** to dispatch through `std::visit`. The variant was supposed to avoid vtables, but the dispatch mechanism creates one anyway. Worse, the two stack stores before the call build a lambda capture struct that the callee has to read back -- overhead that virtual dispatch avoids by passing arguments in registers.

But what does the called function look like, and where does `_S_vtable` come from? To answer that, we need to look inside the standard library.

## Inside the Stdlib: How `std::visit` Dispatches

The assembly above is a consequence of specific implementation choices in libstdc++. Let's look at what's happening inside the standard library.

### libstdc++ (GCC 11.4): The Vtable Builder

The dispatch path in [libstdc++ 11.4's `<variant>` header](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-11.4.0) starts at `std::visit` (line 1734) and flows through three layers:

**Layer 1: `std::visit`** checks for valueless state, then delegates to `__do_visit`:

```cpp
// libstdc++ 11.4, <variant> line 1734
template<typename _Visitor, typename... _Variants>
  visit(_Visitor&& __visitor, _Variants&&... __variants)
  {
    // valueless check -- more on this later
    if ((__variant::__as(__variants).valueless_by_exception() || ...))
      __throw_bad_variant_access("std::visit: variant is valueless");

    return std::__do_visit<_Tag>(
      std::forward<_Visitor>(__visitor),
      __variant::__as(std::forward<_Variants>(__variants))...);
  }
```

**Layer 2: `__do_visit`** looks up the function pointer from a compile-time generated table:

```cpp
// libstdc++ 11.4, <variant> line 1721
template<typename _Result_type, typename _Visitor, typename... _Variants>
  __do_visit(_Visitor&& __visitor, _Variants&&... __variants)
  {
    constexpr auto& __vtable = __gen_vtable<
      _Result_type, _Visitor&&, _Variants&&...>::_S_vtable;

    auto __func_ptr = __vtable._M_access(__variants.index()...);
    return (*__func_ptr)(std::forward<_Visitor>(__visitor),
                         std::forward<_Variants>(__variants)...);
  }
```

**Layer 3: `__gen_vtable`** is where the work happens. It builds a compile-time `_Multi_array` -- an N-dimensional array of function pointers, one dimension per variant argument. For our case (one variant with 3 alternatives), it's a simple 1D array of 3 function pointers:

```cpp
// libstdc++ 11.4, <variant> line 1048
template<typename _Result_type, typename _Visitor, typename... _Variants>
  struct __gen_vtable
  {
    using _Array_type =
      _Multi_array<_Result_type (*)(_Visitor, _Variants...),
                   variant_size_v<remove_reference_t<_Variants>>...>;

    static constexpr _Array_type _S_vtable
      = __gen_vtable_impl<_Array_type, std::index_sequence<>>::_S_apply();
  };
```

Each entry points to a `__visit_invoke` function that calls `std::__invoke` with the correct alternative extracted via `__variant::__get<N>`. The demangled symbol names confirm this -- GCC generates three `__visit_invoke` specializations, one for each index:

```
std::__detail::__variant::__gen_vtable_impl<
  _Multi_array<...>, index_sequence<0>>::__visit_invoke(...)  // EpsilonBS
std::__detail::__variant::__gen_vtable_impl<
  _Multi_array<...>, index_sequence<1>>::__visit_invoke(...)  // SerialBS
std::__detail::__variant::__gen_vtable_impl<
  _Multi_array<...>, index_sequence<2>>::__visit_invoke(...)  // G1BS
```

At runtime, `_M_access(index...)` is just an array subscript: `_M_arr[index]`. Simple enough. But each `__visit_invoke` receives the visitor and variant as arguments, which means the lambda capture must be materialized on the stack before the call. Here's what `__visit_invoke` generates for the most complex strategy (index 2, which reads the old value, stores the new one, and writes two side-effect flags):

```nasm
__visit_invoke<..., index_sequence<2>>:       ; G1BS visitor (read + store + side effects)
    movq  8(%rdi), %rax          ; ← read loop counter ptr from lambda capture
    movq  (%rax), %rcx           ;   load its value
    ; ... 5 instructions computing heap[i % 64] (signed modulo via shift-and-mask) ...
    movq  (%rdi), %rdx           ; ← read heap pointer from lambda capture
    leaq  (%rdx,%rax,4), %rax    ;   compute &heap[i % 64]
    movl  (%rax), %edx           ;   read old value
    movl  %ecx, (%rax)           ;   store new value
    movl  %edx, sink(%rip)       ;   write old value to volatile (side effect)
    movl  $1, sink(%rip)         ;   write flag to volatile (side effect)
    ret
```

The two lines marked with arrows are the cost. They read captured references back from the struct that the caller just built on the stack. The virtual dispatch version passes these in registers directly -- no stack round-trip. That store-then-load pattern, repeated 100M times, is where most of the 28% overhead lives.

### libc++ (Clang/LLVM): Pure Function Pointer Table

libc++ uses a different mechanism that produces a similar result. In [libc++ 19.1's `<variant>` header](https://github.com/llvm/llvm-project/blob/llvmorg-19.1.0/libcxx/include/variant), dispatch goes through `__make_fmatrix` -- a compile-time function that builds a nested array of function pointers (called `__farray`):

```cpp
// libc++ 19.1, <variant> (simplified)
template <class _Visitor, class... _Vs>
  constexpr auto __make_fmatrix() {
    return __make_fmatrix_impl<_Visitor, _Vs...>(index_sequence<>{});
  }
```

The key difference: libc++ **always** uses this function pointer table approach, regardless of variant size. Even a `variant<A, B>` with just two alternatives goes through a table lookup and an indirect call through a function pointer.

Why does this matter? The compiler **cannot inline through function pointer calls** as effectively as through a `switch` statement. When the compiler sees `switch (index) { case 0: ...; case 1: ...; }`, it can inline the visitor body directly into each case. When it sees `table[index](visitor, variant)`, the call target is opaque -- it's just an address.

If you compile the same benchmark with Clang 19 and libc++ on [Compiler Explorer](https://godbolt.org/), you can see the difference: the hot loop looks structurally similar to the libstdc++ version (discriminant load, indexed call), but the `__farray` table and the `__dispatch` trampolines are generated differently. The critical point is the same -- an indirect call through a function pointer that the optimizer cannot see through.

This distinction is significant enough that Michael Park (who [wrote libc++'s variant implementation](https://github.com/llvm/llvm-project/commit/6169a59c51) in 2016) later demonstrated in his standalone [mpark::variant](https://github.com/mpark/variant) library that a switch-based approach is 2-4x faster than the table approach for small variants. libstdc++ added a switch optimization in GCC 12. libc++ never did.

## The `valueless_by_exception` Tax

There's one more cost hidden in every `std::visit` call. Before dispatching, `std::visit` checks whether the variant is in a `valueless_by_exception` state:

```cpp
// libstdc++ 11.4, <variant> line 1738
if ((__variant::__as(__variants).valueless_by_exception() || ...))
    __throw_bad_variant_access("std::visit: variant is valueless");
```

A variant becomes valueless when a type-changing assignment's move/copy constructor throws. The old value has already been destroyed, but the new one failed to construct, leaving the variant in a "neither" state. `std::visit` must check for this and throw `bad_variant_access` if it encounters one.

For our benchmark, **this check can never fire**. All three alternative types (`EpsilonBS`, `SerialBS`, `G1BS`) are trivially copyable structs smaller than 256 bytes. A type-changing assignment for these types uses a temporary and a `memcpy` -- it cannot throw. The variant physically cannot become valueless.

libstdc++ knows this. Since [GCC 9](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-9.1.0#l370), it has a `_Never_valueless_alt` trait:

```cpp
// libstdc++ 11.4, <variant> line 376
template<typename _Tp>
  struct _Never_valueless_alt
  : __and_<bool_constant<sizeof(_Tp) <= 256>, is_trivially_copyable<_Tp>>
  { };
```

When all alternatives satisfy this trait, `__never_valueless()` returns `true`. In our benchmark, GCC 11 at `-O2` sees through the inline expansion of `valueless_by_exception()`, determines it always returns `false`, and eliminates the check entirely from the hot loop. So our benchmark doesn't actually pay this cost.

But change one alternative to a non-trivially-copyable type -- say, a struct containing `std::string` -- and the check appears:

```nasm
; std::visit with variant<Light, Heavy> where Heavy contains std::string
    movsbq  32(%rdi), %rax       ; load variant index (sign-extended)
    cmpb    $-1, %al             ; compare against variant_npos (-1)
    je      .L15                 ; jump to __throw_bad_variant_access
    ; ... dispatch continues ...
```

That `cmpb $-1` / `je` pair runs on every visit call, branching to a cold path that throws. The CPU branch predictor handles it well (always not-taken), but it's an instruction pair that has no equivalent in virtual dispatch -- a vtable pointer is always valid if the object exists.

libc++ has no equivalent `_Never_valueless_alt` optimization. It always emits the check, even for trivially copyable types where the variant physically cannot become valueless.

## The Fundamental Trade-off

Putting it all together:

| Aspect | Virtual dispatch | `std::visit` |
|--------|-----------------|------------|
| **Indirection** | 1 vptr load + 1 vtable entry (2 dependent loads) | discriminant load + table index + function pointer call |
| **Argument passing** | Registers (`%rdi` = this, `%rsi`/`%rdx` = args) | Stack (lambda capture struct built per call) |
| **Branch prediction** | Monomorphic sites predict perfectly | Same after warmup -- single target from same call site |
| **Inlinability** | Compiler can devirtualize known types | Function pointer calls are opaque to optimizer |
| **Valueless check** | None -- vtable pointer is always valid | Elided for trivially copyable types (libstdc++); always present (libc++) |
| **Storage** | Heap-allocated, pointer indirection | Stack-allocated, cache-local |

Notice that branch prediction is effectively the same for both in this benchmark -- both dispatch to a single target after the first iteration. The 28% overhead comes almost entirely from the **argument passing difference**: virtual dispatch passes arguments in registers, while `std::visit` builds a lambda capture struct on the stack every iteration and the callee reads it back. That's two extra stores and two extra loads per call, on a function body that's only about 10 instructions long.

The last row is the one that makes `std::variant` attractive: no heap allocation, no pointer indirection for the data itself. For access patterns that read the stored value frequently but dispatch infrequently, variant wins. But for dispatch-heavy hot paths where every call goes through `std::visit`, the dispatch mechanism's overhead outweighs the storage advantage.

For example, if you're pattern-matching a variant of message types once per network packet, the dispatch cost is negligible and variant's exhaustive type checking is the clear win. If you're dispatching a strategy 100 million times per second in a tight loop, every unnecessary stack spill shows up in the profile.

The core irony: `std::variant` is a zero-cost type-safe union. `std::visit` is the dispatch mechanism layered on top. Combining them introduces costs that a plain vtable doesn't have. "Zero-cost abstraction" does not mean "zero-cost composition."

---

*All benchmarks and source code are in the [companion repository](https://github.com/Shubhankar-Gambhir/cpp-dispatch-benchmark). Measured on Intel Xeon Gold 6130, GCC 11.4, libstdc++, `-O2 -march=skylake-avx512`, pinned to a single core with `taskset -c 0`. Best of 3 runs reported. Stdlib source references: [libstdc++ 11.4 `<variant>`](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/include/std/variant;hb=releases/gcc-11.4.0), [libc++ 19.1 `<variant>`](https://github.com/llvm/llvm-project/blob/llvmorg-19.1.0/libcxx/include/variant).*

---

**Previously:** [Lazy Resolution: Resolve Once, Dispatch Forever]({% post_url 2026-05-12-lazy-resolution-resolve-once-dispatch-forever %}) -- self-patching function pointers that resolve on first call and dispatch at zero cost forever after.

## What's Next

These results are from GCC 11 and libstdc++. They don't have to be this way.

GCC 12 added a switch-based optimization for `std::visit` that eliminates the function pointer table entirely for variants with 11 or fewer alternatives. The threshold has a name in the source: `constexpr size_t __max = 11; // "These go to eleven."` MSVC's standard library has had graduated switch dispatch since before the STL was open-sourced in 2019. libc++ still hasn't added a switch path after nine years.

In the next post, we'll trace the full optimization history of `std::visit` across three standard libraries from 2017 to today, rerun the benchmarks across compiler versions, and show you exactly how much the numbers change when the stdlib catches up.
