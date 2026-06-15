# Design Spec: Part 7 -- The Alignment Cliff (DSB / micro-op cache deep-dive)

Status: approved (design), pending spec review
Author: Shubhankar Gambhir (with Claude)
Date: 2026-06-15
Planned publish date: 2026-06-16 (adjust at publish time)

## Purpose

Fulfill the explicit promise at the end of Part 6 ("The 0.48 ns Ghost",
2026-06-10, line 220):

> "Next time: a standalone deep-dive that isolates the alignment effect with a
> minimal, single-function benchmark, walks through the full DSB and uop-cache
> mechanics on Skylake, and provides a cross-compiler flag reference for GCC,
> Clang, and MSVC."

This spec defines that post. It is **Part 7** of the dispatch series but is
written to **stand alone** (no required back-reading; series footer nav only).

## Audience

C++ performance engineers and anyone who writes or consumes microbenchmarks.
Assume strong C++ and basic CPU pipeline familiarity, but explain the Skylake
front-end from first principles. No dependency on having read Parts 1-6.

## What the post must deliver (the three promised items)

1. A **minimal single-function benchmark** that isolates the alignment effect
   with exactly one variable: the function's entry offset within a cache line.
2. The **full Skylake front-end / DSB / micro-op-cache mechanics**, deeper than
   Part 6's partial sketch, tied directly to the measured curve.
3. A **GCC / Clang / MSVC alignment flag reference**, verified against real
   compiler output (not asserted from memory).

## Non-goals (avoid rehashing Part 6)

- Do not re-run the four-mechanism dispatch matrix. Part 6 owns that.
- Do not re-derive the "codegen vs alignment confound" narrative. Reference it.
- Production guidance is a brief restatement, not a new treatment.

## Narrative approach (chosen: experiment-first, "Approach A")

Open cold with the hook: one trivial leaf function, byte-for-byte identical
machine code, runs at materially different speeds depending only on its entry
address. Show the performance-vs-offset curve in the first screen. Then explain
the front-end mechanics that produce the curve's shape, confirm with perf
counters, give the cross-compiler flag reference, and close with production
guidance and benchmarking hygiene. Data leads; theory explains what was seen.

## The experiment (data plan, run on the Xeon)

Host: Intel Xeon Gold 6130 @ 2.10 GHz (xeongoldgc01.azulsystems.com), single
core via `taskset -c 0`. perf present (4.18 era). This is mandatory: the post is
about Skylake; the local dev host is AMD EPYC and must not be used for numbers.

- **Function under test:** a `__attribute__((noinline))` leaf doing a tiny fixed
  amount of escaped integer work (enough to call in a hot loop; small enough
  that entry-point placement dominates the per-call cost). Result forced to
  survive the optimizer (volatile sink / asm escape).
- **Offset control:** prepend a controlled-length NOP shim via inline `asm`
  (e.g. `.byte 0x90` x N) so the function entry lands at a chosen offset within
  the 64-byte line. Compile the unit with `-falign-functions=1` so the compiler
  does not insert its own padding and defeat the shim. Method mirrors the
  established easyperf / Denis Bakhvalov code-alignment experiment (cite it).
- **Verify placement, do not trust it:** for every generated binary, dump the
  actual function address with `nm` / `objdump -d` and confirm the real offset
  within the 64-byte line. Same "verify the address" discipline as Part 6.
- **Sweep:** offsets 0, 8, 16, 24, 32, 40, 48, 56 bytes (add finer steps around
  any cliff if the curve warrants). 100M iterations, 1M warmup, best of 3 runs.
  Report ns/call per offset.
- **perf counters** at a representative good vs bad offset:
  `idq.dsb_uops`, `idq.mite_uops`, `dsb2mite_switches`, L1i misses, plus
  cycles + instructions for IPC. Goal: show the DSB->MITE fallback directly.
- **Compilers (both measured):**
  - GCC 15.2.0 (conda-forge; needs `-static` on this host due to glibc).
  - Clang (latest conda-forge; pin exact version), installed on the Xeon via
    micromamba for this post.
  - Flags: `-O2 -march=skylake-avx512 -fcf-protection` for both, plus the
    `-falign-functions=1` + NOP-shim offset control described above.
  - Produce two offset curves (GCC, Clang) shown side by side.

## Mechanism section (the "full DSB" deliverable)

Walk the Skylake front-end end to end:
L1i (32 KB, 64-byte lines) -> IFU (16 aligned bytes/cycle) -> predecode ->
the MITE legacy decode path vs the DSB decoded-micro-op cache (32 sets x 8 ways
x 6 uops = 1536 uops; organized in 32-byte windows; max 3 ways per 32-byte
window; up to 6 uops/cycle delivery) -> IDQ -> LSD (loop stream detector).

Then explain the curve: cliffs appear at 32-byte and 64-byte window boundaries
where a small function fragments across DSB windows, can exceed the
3-ways-per-window limit, and forces partial DSB delivery or a DSB->MITE switch
(`dsb2mite_switches`), dropping front-end throughput. The perf counters are the
proof. Sources to cite: Intel 64 and IA-32 Optimization Reference Manual
(front-end / DSB sections), Agner Fog microarchitecture guide (Skylake),
easyperf code-alignment articles, WikiChip Skylake front-end.

## Cross-compiler flag reference (deliverable 3)

A comparison table plus prose. Verify Clang and MSVC behavior on Compiler
Explorer before writing; do not assert from memory.

- **GCC:** `-falign-functions=N`, `-falign-loops`, `-falign-jumps`,
  `-falign-labels`; `__attribute__((aligned(N)))`; defaults under
  `-march=skylake-avx512`.
- **Clang/LLVM:** confirm the supported surface (expected: `-falign-functions=N`
  plus `-mllvm -align-all-functions=N` / `-mllvm -align-all-nofallthru-blocks`;
  `__attribute__((aligned(N)))`). Verify exact spellings.
- **MSVC:** the accurate story -- there is no per-function code-alignment switch
  analogous to GCC's. `__declspec(align(N))` aligns *data*, not function code.
  Function placement is influenced via `/Gy` (COMDAT function-level linking),
  linker `/ALIGN` (section alignment), `/FUNCTIONPADMIN` (hotpatch padding), and
  `#pragma code_seg`. Verify and state precisely; flag the common misconception.

## Production guidance (brief)

Do not enable `-falign-functions=64` globally in production (I-cache + iTLB
cost; covered in Part 6 -- restate in one or two sentences). Apply
`__attribute__((aligned(64)))` to measured hot functions instead. For
benchmarks, align globally. New angle: the offset sweep is itself a tool -- run
it to measure how susceptible a given microbenchmark is, and confirm 64-byte
alignment lands you past the cliffs.

## Companion repo additions (real, reproducible)

In `~/tmp/cpp-dispatch-benchmark/`:

- `benchmarks/bench_alignment_sweep.cpp` -- minimal function + NOP-shim harness.
- `scripts/run_offset_sweep.sh` -- builds the offset family (GCC + Clang),
  verifies placement via `nm`/`objdump`, runs the sweep, emits a CSV.
- `scripts/perf_offset.sh` -- perf counters at good/bad offsets.
- `results/` -- raw CSVs + a generated markdown table for the post.

Commit these so every number in the post is reproducible, matching the series
ethos. (The blog `docs/` directory is excluded from the Jekyll build, so this
spec is not published.)

## Post outline (section by section)

1. Hook + the offset curve (same code, different address, different speed).
2. The experiment: minimal function, NOP-shim offset control, methodology.
3. Reading the curve: where the cliffs are.
4. The Skylake front-end, in full (the mechanism).
5. perf counters confirm the DSB->MITE fallback.
6. GCC vs Clang: do they pad the same? (two curves).
7. Cross-compiler flag reference (GCC / Clang / MSVC table).
8. Production guidance + benchmarking hygiene.
9. Footer: companion repo link, hardware/methodology block, series nav
   (series start + Previously: Part 6), end-of-post newsletter CTA (already in
   the post layout).

House-style requirements: 2500-3000 words; no em dashes (use spaced `--` or
reword); no emojis; pinned compiler versions; AT&T assembly with annotation;
Compiler Explorer link for each assembly snippet; full methodology block
(hardware, compilers, flags, iterations, warmup, runs); markdown tables for
data; per-post OG card via the generator; `image:` front matter with
`hero: false`.

## Success criteria

- Reproducible cliff at 32B/64B window boundaries, best-of-3, with placement
  confirmed by address dump (deterministic, not ASLR noise).
- perf counters at the bad offset show elevated `dsb2mite_switches` /
  `idq.mite_uops` and reduced `idq.dsb_uops` vs the good offset.
- Both GCC and Clang exhibit the effect (two curves).
- Clang and MSVC flag reference verified against real compiler output.
- Jekyll builds clean; post passes `/review-blog` 8-dimension audit.
- Methodology, footer, Compiler Explorer links, OG card all present.

## Risks and fallbacks (surface, do not paper over)

- perf 4.18 on the Xeon may not know the symbolic IDQ event names; resolve via
  `perf list` and fall back to raw Skylake event codes
  (`cpu/event=0x79,umask=.../`). If a specific counter is unavailable, report
  what is available and say so.
- If Clang cannot be installed/run on the Xeon, downgrade Clang to
  assembly-and-flags reference only (Compiler Explorer) and state that in the
  post -- do not fabricate a Clang curve.
- Cliff magnitude depends on function size. Pick a size that demonstrates the
  effect clearly and state the size dependence explicitly; optionally note a
  second size qualitatively without a full second sweep.
- The NOP shim must survive optimization and not be reordered; verify with the
  address dump on every binary before trusting any timing.

## Open questions (resolved during execution, not blockers)

- Exact Clang version available via conda-forge on the Xeon (pin at install).
- Final offset granularity (8-byte steps vs finer near the cliff) -- decide from
  the first curve.
- Final title selection from the candidates in the design.
