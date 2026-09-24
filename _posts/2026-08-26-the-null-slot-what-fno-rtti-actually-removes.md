---
title: "The Null Slot: What -fno-rtti Actually Removes"
date: 2026-08-26
categories: [C++, Performance]
tags: [rtti, dynamic-cast, typeid, abi, vtable, openjdk, hotspot, llvm, gcc, elf, relocations, benchmarks]
description: >-
  Everyone says -fno-rtti disables dynamic_cast and typeid. That is about a third of what
  it does. The vtable does not get smaller, the typeinfo slot stays and goes null, exceptions
  keep emitting typeinfo anyway, and the flag buys exactly zero compile time. Here is what it
  costs, what OpenJDK uses instead, and the mixing failure mode the manual never mentions.
image:
  path: /assets/img/og/the-null-slot-what-fno-rtti-actually-removes.png
  alt: "The Null Slot: What -fno-rtti Actually Removes"
  hero: false
---

Everyone describes `-fno-rtti` the same way: it disables `dynamic_cast` and `typeid`. That is true, and it is about a third of what the flag does.

The part that surprised me is that the vtable does not get any smaller. Compile a polymorphic class with the flag and the typeinfo pointer is still there, still occupying its eight bytes, just holding null. That one detail explains the flag's nastiest failure mode.

I went looking because OpenJDK builds `libjvm.so` with it and I wanted to know what that actually buys. This post is the answer: what the flag does to the ABI, what it still lets you write, what adding RTTI back would cost, and what HotSpot uses in place of `dynamic_cast`. Everything here reproduces from [the companion repo](https://github.com/Shubhankar-Gambhir/fno-rtti-bench) with `make`.

## The Slot Stays, and Goes Null

Compile a two-class hierarchy both ways and diff the symbols:

```
=== WITH RTTI ===                              === WITHOUT RTTI ===
U vtable for __cxxabiv1::__class_type_info     (gone)
U vtable for __cxxabiv1::__si_class_type_info  (gone)
V typeinfo for Base                            (gone)
V typeinfo for Derived                         (gone)
V typeinfo name for Base                       (gone)
V typeinfo name for Derived                    (gone)
V vtable for Base                              V vtable for Base
V vtable for Derived                           V vtable for Derived
```

The typeinfo objects go, and so do the undefined references to libsupc++'s runtime vtables. The vtables themselves stay, and so does their size:

```
poly_rtti.o    .data.rel.ro.local._ZTV4Base   size 0x28   4 relocations
poly_nortti.o  .data.rel.ro.local._ZTV4Base   size 0x28   3 relocations
```

Forty bytes either way. The missing relocation is precisely the typeinfo slot:

```
WITH RTTI                                  WITHOUT RTTI
+0x00   (offset-to-top)                    +0x00   (offset-to-top)
+0x08   -> _ZTI4Base                       +0x08   no relocation, stays NULL
+0x10   -> _ZN4BaseD1Ev   <- vptr points here
+0x18   -> _ZN4BaseD0Ev
+0x20   -> _ZN4Base1fEv
```

You can confirm it at runtime. Read `vptr[-1]` on an object whose vtable came from a `-fno-rtti` translation unit and you get `(nil)`.

None of this is a GCC 11 quirk, before anyone asks. The same probe on GCC 9.5, 10.4, 11.4, 12.4, 13.4, 14.3, 15.2 and Clang 22.1.4 gives a `0x28` vtable in every one of them, with the flag and without it, and zero surviving `_ZTI` symbols in every `-fno-rtti` object. The layout is pinned by the Itanium C++ ABI, not by a compiler release.

So the flag is layout-ABI-preserving. Virtual function slots sit at identical offsets, objects are the same size, and virtual dispatch generates bit-identical code. It is a content change, one pointer going null, not a shape change.

That choice is deliberate and worth appreciating. Had the flag reclaimed the eight bytes, every `-fno-rtti` object file would be silently incompatible with every `-frtti` one at the vtable level, and the symptom would be arbitrary wrong-function calls rather than a null dereference. Keeping the slot trades eight bytes per class for a failure mode you can debug. Clang's newer `-fexperimental-omit-vtable-rtti` does reclaim it, which is why it is gated behind "experimental" and refuses to run without `-fno-rtti`. Its existence is the cleanest confirmation that plain `-fno-rtti` leaves the slot alone.

## What It Actually Rejects

| Construct | GCC 11.4.0 with `-fno-rtti` |
|---|---|
| `typeid(*polymorphic_ptr)` | error: cannot use 'typeid' with '-fno-rtti' |
| `typeid(SomeType)`, static, no runtime lookup | error, GCC rejects this too |
| `dynamic_cast<Derived*>(base_ptr)`, downcast | error: 'dynamic_cast' not permitted with '-fno-rtti' |
| `dynamic_cast<Base*>(derived_ptr)`, upcast | compiles clean |
| `dynamic_cast<void*>(p)` | allowed |
| `throw` / `catch`, including catch-by-base | works completely |

Two of these catch people out.

Upcasts survive because they are a compile-time offset adjustment. There is no runtime machinery to disable.

Exceptions are the bigger one. They keep working, and they keep emitting typeinfo:

```
$ g++ -fno-rtti -O2 -c eh.cpp && nm -C eh.o | grep typeinfo
V typeinfo for MyErr
V typeinfo for Sub
V typeinfo name for MyErr
V typeinfo name for Sub
```

The Itanium C++ ABI matches catch handlers by comparing `std::type_info` pointers, so the compiler emits typeinfo on demand no matter what you asked for. GCC's manual says so outright: "exception handling uses the same information, but G++ generates it as needed." This is why every serious user of the flag pairs it with `-fno-exceptions`. Alone, `-fno-rtti` leaves a great deal on the table.

The compilers also disagree on the boundaries. Clang's `-fno-rtti-data` suppresses the data but still permits `typeid`; MSVC's `/GR-` lets `typeid` compile and then fails at runtime. Code that builds under `/GR-` will not necessarily build under `-fno-rtti`.

## The Failure Mode Nobody Documents

Mixing `-frtti` and `-fno-rtti` objects fails in two directions, and they are not equally kind.

Direction A is an `-frtti` class deriving from an `-fno-rtti` base. Loud link error:

```
ld: dd.o:(.data.rel.ro._ZTI2D2[typeinfo for D2]+0x10):
    undefined reference to `typeinfo for Base'
collect2: error: ld returned 1 exit status
```

Fine. You find out immediately, and this is the case the GCC manual warns about.

Direction B is `-frtti` code calling `typeid` or `dynamic_cast` on an object whose vtable came from an `-fno-rtti` TU:

```
$ g++ lib_nortti.o app_rtti.o -o app_n     # links successfully, no diagnostic
$ ./app_n
vptr[1] (typeinfo slot) = (nil)
about to call typeid...
Segmentation fault (core dumped)
```

No linker diagnostic, no warning, just a null dereference inside `__dynamic_cast` at runtime in code that reads correctly. This is the preserved-but-null slot earning its keep: a clean crash instead of a wrong-function call. Better, certainly, but still a crash with no build-time signal.

Direction B is the one that will hurt you, and it is the one the manual does not mention. Ship a `-fno-rtti` library with public polymorphic types and every downstream consumer building with default flags is one `dynamic_cast` away from this. Whether a library is built `-fno-rtti` is part of its public API contract and belongs in its headers.

## What It Costs

Start with something real. A stock OpenJDK 17.0.0.1 `libjvm.so`:

```
_ZTV (vtables)     3157
_ZTI (typeinfo)      10        <- 315:1
```

All ten survivors are libsupc++ and libstdc++ internals: `__class_type_info`, `std::bad_exception`, `__forced_unwind`. HotSpot's own three thousand polymorphic classes contribute exactly zero. Counting `_ZTI` against `_ZTV` turns out to be a decent one-line test for whether any binary was built this way.

To find out what those 3157 null slots are worth, I cloned HotSpot's `BarrierSet` hierarchy shape into 1800 polymorphic classes and built it three ways, keeping the two flags separate:

| metric | `-frtti` | `-fno-rtti` | `+ -fno-exceptions` |
|---|---:|---:|---:|
| file size (bytes) | 2,747,984 | 2,160,928 (-21.4%) | 2,082,856 (-24.2%) |
| dynamic relocations | 16,219 | 9,016 (-44.4%) | 9,014 (-44.4%) |
| `.rodata`, the `_ZTS` name strings | 159,628 | 44,716 (-72.0%) | 44,716 |
| `.data.rel.ro`, the `_ZTI` objects | 144,072 | 100,856 (-30.0%) | 100,856 |
| `.gcc_except_table` | 23,656 | 23,656 (0%) | 0 (-100%) |
| `_ZTI` symbols | 1,801 | 0 | 0 |
| compile wall time, best of 5 | 2.92 s | 2.86 s (0%) | 1.50 s (-49%) |

Two rows deserve attention. `-fno-rtti` does not speed up compilation; across five passes each the two distributions overlapped completely. The build-time win people attribute to this flag pair comes entirely from `-fno-exceptions`, which also owns `.gcc_except_table` outright. The flags are complementary rather than redundant, and quoting the pair's savings for one of them is how the folklore got muddled.

The 7,203 vanished relocations divide evenly, exactly four per class: the vtable's typeinfo slot, the `_ZTI` object's own vptr, its `__name` pointer, and its `__base_type` pointer. That yields per-class rates that transfer:

```
_ZTS name strings   (.rodata)       63.8 B/class
_ZTI objects        (.data.rel.ro)  24.0 B/class
dynamic relocations (.rela.dyn)     96.0 B/class   (4.00 relocs/class)
                                   -----------------
                                   183.8 B/class
```

Relocations cost more than the objects they point at, and they are the part people forget. Each one is processed at load time and dirties a page in `.data.rel.ro` that the process can no longer share. On this corpus that comes to a 21 percent faster `dlopen`, 295 down to 232 microseconds at the median of 400, and 30 percent fewer private-dirty pages, 148 KB down to 104 KB.

The percentages do not transfer, and I want to be clear about that. The synthetic library is almost pure vtable, since every method body is `return N;`. A real `libjvm.so` is overwhelmingly code, so the same per-class rates land at 3157 x 184 B, roughly 580 KB on a 21.7 MiB binary. Two and a half percent, not twenty-one. Take the per-class byte counts and the four-relocations-per-class rate; leave the percentages here.

## What OpenJDK Does Instead

Nobody turns RTTI off and then goes without a substitute. HotSpot's is called, with admirable honesty, `FakeRtti`.

`BarrierSet` is the canonical example, the GC write-barrier interface, with `G1BarrierSet` three levels below the base and `ZBarrierSet`, `ShenandoahBarrierSet`, and `EpsilonBarrierSet` as siblings. Every class gets a bit, and each constructor adds its own on the way up:

```cpp
typedef FakeRttiSupport<BarrierSet, Name> FakeRtti;

bool is_a(BarrierSet::Name bsn) const { return _fake_rtti.has_tag(bsn); }

template<typename T> inline T* barrier_set_cast(BarrierSet* bs) {
  assert(bs->is_a(BarrierSet::GetName<T>::value), "wrong type of barrier set");
  return static_cast<T*>(bs);          // assert compiled out in product
}
```

A `G1BarrierSet` ends up carrying `_tag_set = 0b0111`: its own bit, plus `CardTableBarrierSet`, plus `ModRef`. Asking whether it is a `CardTableBarrierSet` is one mask against a constant.

Here is the whole argument in three disassemblies, from a product build compiled `-frtti` so all three forms are available side by side ([Compiler Explorer](https://godbolt.org/z/zYfbPfxK1)):

```asm
; barrier_set_cast<CardTableBarrierSet>(bs) -- what HotSpot ships
    mov    %rdi,%rax          ; return the pointer unchanged
    ret                       ; the assert is gone under NDEBUG

; bs->is_a(BarrierSet::CardTableBarrierSet) -- the checked form
    xor    %edx,%edx          ; edx = 0, the null result
    mov    %rdi,%rax          ; rax = bs
    testb  $0x2,0x8(%rdi)     ; 1 << CardTableBarrierSet, against _tag_set
    cmove  %rdx,%rax          ; branchless: zero rax if the bit was clear
    ret

; dynamic_cast<CardTableBarrierSet*>(bs)
    test   %rdi,%rdi
    je     ...                ; null in, null out
    xor    %ecx,%ecx          ; hint = 0, no statically known offset
    lea    0x0(%rip),%rdx     ; &typeinfo for CardTableBarrierSet
    lea    0x0(%rip),%rsi     ; &typeinfo for BarrierSet
    jmp    ...                ; tail-call __dynamic_cast, out of line
```

Timing them against a single `BarrierSet*` whose dynamic type is `G1BarrierSet`, which is the default JVM's hot path:

| operation | ns/op | vs `is_a()` |
|---|---:|---:|
| baseline, no cast | 0.48 | |
| `bs->write_ref_field_work()`, a virtual call, for scale | 2.39 | |
| `barrier_set_cast<CardTableBarrierSet>` | 0.48 | |
| `bs->is_a(CardTableBarrierSet)` | 1.91 | 1x |
| `dynamic_cast<G1BarrierSet*>`, exact-type hit | 5.74 | 3.0x |
| `dynamic_cast<CardTableBarrierSet*>`, base-type hit | 30.20 | 15.8x |
| `dynamic_cast<ZBarrierSet*>`, miss | 55.72 | 29.2x |

A caveat on the top four rows before anyone quotes them at me. At 2.10 GHz one cycle is 0.476 ns, and those four numbers are 1, 5, 1, and 4 cycles exactly. They quantize, and they move between builds in ways that have nothing to do with the cast: in a `-fno-rtti` build the baseline reads 2 cycles and `is_a` also reads 2, which would make `is_a` free. The honest statement is that `barrier_set_cast`, `is_a`, and doing nothing sit within a couple of cycles of each other and this harness cannot separate them. It is [the same quantization that had an earlier benchmark of mine lying to me for a week]({% post_url 2026-06-10-the-048-ns-ghost-how-code-alignment-broke-our-dispatch-benchmarks %}). The `dynamic_cast` rows are far enough out to mean something.

What they mean is that `dynamic_cast`'s cost depends on the answer. An exact-type match is the fast path. Casting to a base three levels up costs five times that, because the runtime walks the hierarchy. A miss costs ten times it, because the runtime must exhaust the hierarchy before it can return null. `is_a()` is flat regardless of which question you ask. A failing `dynamic_cast` here runs 23 times a virtual call, in a codebase where [virtual dispatch itself gets scrutinised]({% post_url 2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp %}).

Those figures all use a single dynamic type, which is the friendliest case a branch predictor will ever see. Cycling the pointer through a mixed population of barrier sets moves the numbers without changing the shape: the base-type hit drops to 27.82 ns and the miss to 41.30 ns. Both go down, which is the opposite of what the branch-prediction intuition suggests, and I have not chased down why, so take those two as measurements rather than explanations. `is_a()` does not move. The gap narrows, the ordering holds.

I ran the same benchmark on an AMD EPYC-Rome box, where the absolute nanoseconds differ by roughly a factor of two. The ratios barely move: 16.0x there against 15.8x here for the base-type hit, 30.7x against 29.2x for the miss. Only the exact-type fast path is genuinely machine-dependent, 7.4x there against 3.0x here.

LLVM reached the same design independently. It defaults `LLVM_ENABLE_RTTI` to `OFF`, replaces `dynamic_cast` with the `isa<>`/`cast<>`/`dyn_cast<>` templates, and encodes the exceptions dependency directly in CMake:

```cmake
if(LLVM_ENABLE_EH AND NOT LLVM_ENABLE_RTTI)
  message(FATAL_ERROR "Exception handling requires RTTI. You must set LLVM_ENABLE_RTTI to ON")
endif()
```

That `FATAL_ERROR` is the typeinfo-for-exceptions fact from earlier, written into a build system.

## A Design Flag Wearing a Size Flag's Clothes

Add it up and size is the weakest argument in the pile. Two and a half percent of a real binary, weighed against banning `dynamic_cast`, third-party packages needing `-DGTEST_HAS_RTTI=0` and its equivalents, and UBSan quietly dropping its `vptr` check because that check needs the typeinfo you just deleted. If size is your only reason, `-ffunction-sections -Wl,--gc-sections` pays better and costs nothing socially.

What the flag actually buys is a compiler-enforced invariant: no runtime type introspection anywhere in this binary. That is what makes the C++ safe to run where HotSpot runs it, inside signal handlers for its null checks and safepoint polls, inside GC pauses, against arena and metaspace allocators rather than a normal heap. `__dynamic_cast` calls into libsupc++, which can allocate and take locks, and none of that is acceptable during a pause.

OpenJDK's own build makes the point better than any argument. The flag is not global: `flags-cflags.m4` puts it on `TOOLCHAIN_CFLAGS_JVM` and leaves `TOOLCHAIN_CFLAGS_JDK` alone. You can verify the scoping in the shipped bits without reading a line of source. In the same JDK 17 install, `libjvm.so` has 3157 vtables and no typeinfo of its own, while `libjimage.so` carries typeinfo for `ZipDecompressor`, `ImageDecompressor`, and `Endian`, all of it ordinary C++ compiled with ordinary flags. HotSpot is the constrained-runtime component; the rest of the JDK is not, and does not pay.

So reach for `-fno-rtti` when you already do not use `dynamic_cast`, when you control the whole link unit or are willing to document the contract, and when you are switching off exceptions in the same breath. It should ratify a design decision you already made, not force one. What it should not be is a late-stage retrofit onto a mature codebase to recover two percent.

---

Companion repo: [github.com/Shubhankar-Gambhir/fno-rtti-bench](https://github.com/Shubhankar-Gambhir/fno-rtti-bench)

Hardware: Intel Xeon Gold 6130 @ 2.10 GHz (2x16 cores, Skylake), single-threaded and pinned to one core. The cross-check machine is a 64-core AMD EPYC-Rome VM.
Compilers: timings are GCC 11.4.0 on both machines. The ABI and symbol probes were additionally cross-checked on GCC 9.5.0, 10.4.0, 12.4.0, 13.4.0, 14.3.0, 15.2.0 and Clang 22.1.4 (conda-forge, micromamba), which agree exactly. Flags: `-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64 -DNDEBUG`. The alignment flags were measured against a plain `-O2 -DNDEBUG` build and changed nothing here, unlike in earlier posts in this series; they are kept for consistency.
Methodology: cast microbenchmark is 30M iterations after a 2M warmup, 5 runs, median reported, with an inline asm barrier to stop the optimiser hoisting a loop-invariant cast. Size and relocation figures are static ELF accounting and are exactly reproducible. Compile wall time is best of 5 at `-j64`. `dlopen` figures are the median of 400 loads; private-dirty pages are read from `/proc/self/smaps` after the final load.
JVM figures come from a stock OpenJDK 17.0.0.1 build (`nm -a`, `readelf -rW`); `make jvm` will run the same census against any JDK you point it at.

Previously: [Lock-Free Is Not Free: ABA, Tagged Pointers, and a Bounded Ring]({% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %})
