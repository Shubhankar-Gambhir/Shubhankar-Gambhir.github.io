# Part 7 -- The Alignment Cliff: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and publish Part 7, a standalone deep-dive that isolates code-alignment cost with a minimal single-function offset sweep on the Xeon Gold 6130 (GCC + Clang), explains the full Skylake DSB/micro-op-cache mechanics, and gives a GCC/Clang/MSVC flag reference.

**Architecture:** A two-translation-unit microbenchmark in the companion repo (`~/tmp/cpp-dispatch-benchmark/`): a fixed harness loop calls a tiny `work()` defined in a separate TU whose entry offset is swept 0..56 bytes by injecting a `.skip` NOP shim into the compiler-emitted assembly (compiler self-alignment disabled with `-falign-functions=1`). Offsets are verified per-binary with `nm`. perf front-end counters at a good vs bad offset prove the DSB->MITE fallback. Real numbers feed a house-style blog post in the Jekyll repo.

**Tech Stack:** C/C++17, GCC 15.2.0 + Clang (conda-forge via micromamba on the Xeon), GNU as/ld, `perf`, bash, Python (Pillow for the OG card), Jekyll/Chirpy.

**Repos & hosts:**
- Blog repo (Jekyll): `/home/sgambhir/tmp/shubhankar-gambhir.github.io` (this repo)
- Companion repo: `/home/sgambhir/tmp/cpp-dispatch-benchmark`
- Benchmark host: `xeongoldgc01.azulsystems.com` (Intel Xeon Gold 6130, Skylake-AVX512). The local dev host is AMD EPYC and must NOT be used for any published number.

**No worktree:** purely additive work (new files + one new post) across two repos; frequent commits on `main` in each. No isolation needed.

---

## Task 1: Offset-sweep benchmark sources (companion repo)

**Files:**
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/benchmarks/bench_alignment_sweep.cpp`
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/benchmarks/sweep_work.c`

- [ ] **Step 1: Write the harness (separate TU from the function under test)**

`benchmarks/bench_alignment_sweep.cpp`:
```cpp
// Harness for the code-alignment offset sweep (blog Part 7).
// The function under test, work(), lives in a SEPARATE translation unit
// (sweep_work.c) so that shifting its entry offset does not move this loop.
// Build this object once with fixed alignment; only work()'s offset varies.
#include <cstdio>
#include <ctime>

extern "C" long work(long x);

int main() {
    const long ITERS = 100000000;  // 100M, matches the series
    const long WARM  =   1000000;  // 1M warmup
    long acc = 0;
    for (long i = 0; i < WARM; ++i) acc += work(i);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (long i = 0; i < ITERS; ++i) acc += work(i);
    clock_gettime(CLOCK_MONOTONIC, &t1);

    const double ns = ((t1.tv_sec - t0.tv_sec) * 1e9 +
                       (t1.tv_nsec - t0.tv_nsec)) / (double)ITERS;
    volatile long sink = acc; (void)sink;   // keep acc live
    printf("%.4f ns/call\n", ns);
    return 0;
}
```

- [ ] **Step 2: Write the function under test**

`benchmarks/sweep_work.c`:
```c
/* Function under test for the alignment offset sweep (blog Part 7).
   Deliberately tiny: a few dependent integer ops, so the per-call cost is
   dominated by front-end delivery of the entry point, not the body.
   Lives in its own TU so the harness (no LTO) cannot inline it. */
long work(long x) {
    long y = x * 2654435761L;          /* Knuth multiplicative hash */
    y ^= (unsigned long)y >> 13;
    y *= 1099511628211L;               /* FNV prime */
    return y;
}
```

- [ ] **Step 3: Verify both compile locally (correctness only -- NOT for numbers)**

Run:
```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
g++ -O2 -c benchmarks/bench_alignment_sweep.cpp -o /tmp/h.o && \
gcc -O2 -c benchmarks/sweep_work.c -o /tmp/w.o && \
g++ /tmp/h.o /tmp/w.o -o /tmp/sweep_smoke && /tmp/sweep_smoke
```
Expected: prints one line like `X.XXXX ns/call` (value irrelevant on AMD; we only confirm it links and runs).

- [ ] **Step 4: Commit**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git add benchmarks/bench_alignment_sweep.cpp benchmarks/sweep_work.c
git commit -m "bench: minimal two-TU harness for Part 7 alignment offset sweep"
```

---

## Task 2: Offset-sweep runner script

**Files:**
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/scripts/run_offset_sweep.sh`

- [ ] **Step 1: Write the runner**

`scripts/run_offset_sweep.sh`:
```bash
#!/usr/bin/env bash
# Build the harness once, then build work() at a range of entry offsets within a
# 64-byte cache line and measure ns/call (best of 3) at each. Verifies the
# achieved offset per binary with nm.
#   Usage: run_offset_sweep.sh <CXX> <CC> <TAG> [extra compiler flags...]
#   e.g.:  run_offset_sweep.sh g++ gcc gcc15 -static
set -euo pipefail
CXX="$1"; CC="$2"; TAG="$3"; shift 3
COMMON=( -O2 -march=skylake-avx512 -fcf-protection "$@" )
OFFSETS=(0 8 16 24 32 40 48 56)
mkdir -p build results
OUT="results/offset_sweep_${TAG}.csv"

# Harness object: fixed alignment so its loop never moves between runs.
$CXX "${COMMON[@]}" -falign-functions=64 -falign-loops=64 \
     -c benchmarks/bench_alignment_sweep.cpp -o "build/harness_${TAG}.o"

echo "offset,ns_per_call" > "$OUT"
for N in "${OFFSETS[@]}"; do
  base="build/work_${TAG}_${N}"
  # 1. compile work() to asm with compiler self-alignment OFF
  $CC "${COMMON[@]}" -falign-functions=1 -S benchmarks/sweep_work.c -o "${base}.s"
  # 2. inject ".p2align 6" + ".skip N,0x90" immediately before the work: label
  awk -v n="$N" '
    /^work:/ && !done { print ".p2align 6"; if (n>0) printf ".skip %d,0x90\n", n; done=1 }
    { print }' "${base}.s" > "${base}.shim.s"
  # 3. assemble + link against the fixed harness
  $CC "${COMMON[@]}" -c "${base}.shim.s" -o "${base}.o"
  $CXX "${COMMON[@]}" "build/harness_${TAG}.o" "${base}.o" -o "build/bench_${TAG}_${N}"
  # 4. VERIFY the achieved offset (do not trust intent)
  addr=$(nm "build/bench_${TAG}_${N}" | awk '$3=="work"{print $1}')
  got=$(( 0x$addr % 64 ))
  [ "$got" -eq "$N" ] || echo "WARN ${TAG} want=${N} got=${got} addr=0x${addr}" >&2
  # 5. best of 3
  best=$(for r in 1 2 3; do taskset -c 0 "./build/bench_${TAG}_${N}" | grep -oE '[0-9.]+'; done | sort -g | head -1)
  echo "${N},${best}" | tee -a "$OUT"
done
echo "wrote ${OUT}"
```

- [ ] **Step 2: Make it executable + smoke test locally (script correctness only)**

Run:
```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
chmod +x scripts/run_offset_sweep.sh
# local smoke: AMD host, numbers are throwaway. Drop -march if skylake build won't run here.
ITERS_OK=1 ./scripts/run_offset_sweep.sh g++ gcc smoke -mno-avx512f || true
head -3 results/offset_sweep_smoke.csv
```
Expected: a CSV with header `offset,ns_per_call` and up to 8 rows. The `WARN want!=got` lines must NOT appear (offsets achieved). If they do, fix the awk label match before proceeding. Delete the smoke CSV afterward: `rm -f results/offset_sweep_smoke.csv build/*smoke*`.

- [ ] **Step 3: Commit**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git add scripts/run_offset_sweep.sh
git commit -m "bench: offset-sweep runner with per-binary offset verification (Part 7)"
```

---

## Task 3: perf counter script

**Files:**
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/scripts/perf_offset.sh`

- [ ] **Step 1: Write the perf script (symbolic events, raw-code fallback)**

`scripts/perf_offset.sh`:
```bash
#!/usr/bin/env bash
# Capture Skylake front-end counters for two pre-built sweep binaries (a good
# and a bad offset). Resolves event names; falls back to raw codes if perf
# does not know the symbolic names.
#   Usage: perf_offset.sh <TAG> <GOOD_OFFSET> <BAD_OFFSET>
set -euo pipefail
TAG="$1"; GOOD="$2"; BAD="$3"
mkdir -p results

# Prefer symbolic names; if `perf list` lacks them, use Skylake raw codes.
pick() { perf list 2>/dev/null | grep -qiw "$1" && echo "$1" || echo "$2"; }
E_DSB=$(pick idq.dsb_uops            'cpu/event=0x79,umask=0x08,name=dsb_uops/')
E_MITE=$(pick idq.mite_uops          'cpu/event=0x79,umask=0x04,name=mite_uops/')
E_SW=$(pick dsb2mite_switches.penalty_cycles 'cpu/event=0xab,umask=0x02,name=dsb2mite_pc/')
E_L1I=$(pick L1-icache-load-misses   'cpu/event=0x83,umask=0x02,name=icache_iftag_miss/')
EVENTS="${E_DSB},${E_MITE},${E_SW},${E_L1I},instructions,cycles"
echo "events: ${EVENTS}"

for label in good:${GOOD} bad:${BAD}; do
  name="${label%%:*}"; off="${label##*:}"
  bin="build/bench_${TAG}_${off}"
  out="results/perf_${TAG}_${name}_off${off}.txt"
  echo "== ${name} (offset ${off}) =="
  taskset -c 0 perf stat -o "$out" -e "$EVENTS" "./${bin}" >/dev/null
  cat "$out"
done
```

- [ ] **Step 2: Make executable + commit (run happens on the Xeon in Task 6)**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
chmod +x scripts/perf_offset.sh
git add scripts/perf_offset.sh
git commit -m "bench: perf front-end counter script for Part 7 (symbolic+raw events)"
```

---

## Task 4: Provision the Xeon and install Clang

**Host:** `xeongoldgc01.azulsystems.com`

- [ ] **Step 1: Sync the companion repo to the Xeon**

Run (from local):
```bash
ssh xeongoldgc01.azulsystems.com 'mkdir -p ~/cpp-dispatch-benchmark'
rsync -az --delete /home/sgambhir/tmp/cpp-dispatch-benchmark/ \
  xeongoldgc01.azulsystems.com:~/cpp-dispatch-benchmark/
```
Expected: completes without error.

- [ ] **Step 2: Install Clang via micromamba on the Xeon; pin version**

Run:
```bash
ssh xeongoldgc01.azulsystems.com '~/utils/mamba/bin/micromamba create -y -n clang -c conda-forge clangxx 2>&1 | tail -5; \
  ~/utils/mamba/envs/clang/bin/clang++ --version'
```
Expected: prints a clang version. **Record the exact version string** (used in the post methodology). If install fails (no network/conda), STOP and report -- per spec we downgrade Clang to assembly/flags-reference only and tell the user.

- [ ] **Step 3: Confirm GCC 15 toolchain + perf on the Xeon**

Run:
```bash
ssh xeongoldgc01.azulsystems.com '~/utils/mamba/envs/gcc15/bin/x86_64-conda-linux-gnu-g++ --version | head -1; perf --version'
```
Expected: GCC 15.2.0 line + a perf version. (No commit in this task.)

---

## Task 5: Run the offset sweep on the Xeon (GCC + Clang)

- [ ] **Step 1: Run GCC sweep**

Run:
```bash
ssh xeongoldgc01.azulsystems.com 'cd ~/cpp-dispatch-benchmark && \
  GCC=~/utils/mamba/envs/gcc15/bin/x86_64-conda-linux-gnu; \
  ./scripts/run_offset_sweep.sh ${GCC}-g++ ${GCC}-gcc gcc15 -static'
```
Expected: 8 rows in `results/offset_sweep_gcc15.csv`, no `WARN want!=got` lines. Sanity: at least one offset should differ from the offset-0 baseline by a visible margin (the cliff); best-of-3 should be stable across re-runs (re-run once to confirm determinism).

- [ ] **Step 2: Run Clang sweep**

Run:
```bash
ssh xeongoldgc01.azulsystems.com 'cd ~/cpp-dispatch-benchmark && \
  CLANGXX=~/utils/mamba/envs/clang/bin/clang++; \
  CLANGC=~/utils/mamba/envs/clang/bin/clang; \
  ./scripts/run_offset_sweep.sh ${CLANGXX} ${CLANGC} clang -static || \
  ./scripts/run_offset_sweep.sh ${CLANGXX} ${CLANGC} clang'
```
Expected: 8 rows in `results/offset_sweep_clang.csv`, no offset warnings. (Try `-static` first; if Clang's static link fails on this host, fall back to dynamic.)

- [ ] **Step 3: Pull results back and inspect**

Run (from local):
```bash
rsync -az xeongoldgc01.azulsystems.com:~/cpp-dispatch-benchmark/results/ \
  /home/sgambhir/tmp/cpp-dispatch-benchmark/results/
column -t -s, /home/sgambhir/tmp/cpp-dispatch-benchmark/results/offset_sweep_gcc15.csv
column -t -s, /home/sgambhir/tmp/cpp-dispatch-benchmark/results/offset_sweep_clang.csv
```
Expected: two readable tables. Identify the GOOD offset (lowest ns) and BAD offset (highest ns) for each -- needed for Task 6. If the curve is flat (no cliff), see Task 5 Step 4.

- [ ] **Step 4 (only if no cliff appears): shrink the function / refine offsets**

If both curves are flat, the body is too large to expose the entry penalty. Reduce `sweep_work.c` to a single multiply + return, re-commit Task 1, re-sync (Task 4 Step 1), re-run. Optionally add finer offsets near a suspected boundary by editing `OFFSETS` to `(0 4 8 ... 60)`. Document whatever final granularity is used.

- [ ] **Step 5: Commit results to the companion repo**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git add results/offset_sweep_gcc15.csv results/offset_sweep_clang.csv
git commit -m "data: Part 7 offset-sweep curves, GCC 15 + Clang (Xeon Gold 6130)"
```

---

## Task 6: Run perf counters on the Xeon

- [ ] **Step 1: Run perf at good vs bad offset (GCC binaries)**

Using the GOOD/BAD offsets identified in Task 5 Step 3 (substitute the real numbers):
```bash
ssh xeongoldgc01.azulsystems.com 'cd ~/cpp-dispatch-benchmark && \
  ./scripts/perf_offset.sh gcc15 <GOOD> <BAD>'
```
Expected: two perf outputs printed and saved to `results/perf_gcc15_good_off<GOOD>.txt` and `..._bad_off<BAD>.txt`. The bad offset should show higher `dsb2mite` and/or `mite_uops` and lower `dsb_uops` than the good offset. If `perf list` lacked symbolic names, confirm the raw-code fallback produced non-zero counts (if zero, the raw umask is wrong for this stepping -- resolve via `perf list | grep -i dsb` on the host and update `scripts/perf_offset.sh`, re-commit).

- [ ] **Step 2: Pull + commit perf results**

```bash
rsync -az xeongoldgc01.azulsystems.com:~/cpp-dispatch-benchmark/results/ \
  /home/sgambhir/tmp/cpp-dispatch-benchmark/results/
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git add results/perf_gcc15_*.txt
git commit -m "data: Part 7 perf front-end counters at good vs bad offset (Xeon)"
```

---

## Task 7: Capture assembly + cross-compiler alignment behavior

**Files:**
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/results/asm_work_gcc15.txt`
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/results/asm_work_clang.txt`
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/results/crosscompiler_align_notes.md`

- [ ] **Step 1: Dump the work() body for each compiler (proves byte-identical across offsets)**

Run (from local, on the synced build dir, or on the Xeon then pull):
```bash
ssh xeongoldgc01.azulsystems.com 'cd ~/cpp-dispatch-benchmark && \
  objdump -d --no-show-raw-insn build/bench_gcc15_0  | sed -n "/<work>:/,/ret/p" > results/asm_work_gcc15.txt; \
  objdump -d --no-show-raw-insn build/bench_clang_0  | sed -n "/<work>:/,/ret/p" > results/asm_work_clang.txt'
rsync -az xeongoldgc01.azulsystems.com:~/cpp-dispatch-benchmark/results/ /home/sgambhir/tmp/cpp-dispatch-benchmark/results/
```
Expected: short AT&T-syntax listings of `work` for each compiler.

- [ ] **Step 2: Empirically check Clang's alignment flags on the Xeon**

Run:
```bash
ssh xeongoldgc01.azulsystems.com '~/utils/mamba/envs/clang/bin/clang --help-hidden 2>/dev/null | grep -iE "align-functions|align-all" ; \
  echo "long f(long x){return x*3;}" > /tmp/a.c; \
  ~/utils/mamba/envs/clang/bin/clang -O2 -falign-functions=64 -c /tmp/a.c -o /tmp/a.o && \
  objdump -d /tmp/a.o | head -5'
```
Expected: confirms which alignment spellings Clang accepts (`-falign-functions=N`, and/or `-mllvm -align-all-functions=N`). Record the verified flags.

- [ ] **Step 3: Write the cross-compiler notes (GCC verified, Clang verified, MSVC from docs)**

`results/crosscompiler_align_notes.md` -- record, with evidence:
- GCC: `-falign-functions/-falign-loops/-falign-jumps/-falign-labels`, `__attribute__((aligned(N)))`, `-march` defaults (already established in Part 6).
- Clang: the exact flags confirmed in Step 2.
- MSVC (documented, no local run): no per-function code-alignment switch analogous to GCC; `__declspec(align(N))` aligns DATA not function code; placement is influenced via `/Gy` (COMDAT), linker `/ALIGN` (section), `/FUNCTIONPADMIN` (hotpatch), `#pragma code_seg`. Mark these as "per MSVC documentation; not measured here."

- [ ] **Step 4: Commit**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git add results/asm_work_gcc15.txt results/asm_work_clang.txt results/crosscompiler_align_notes.md
git commit -m "data: Part 7 work() assembly (GCC/Clang) + cross-compiler alignment notes"
```

---

## Task 8: Generate the curve chart (ASCII, house style)

**Files:**
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/scripts/ascii_curve.py`
- Create: `/home/sgambhir/tmp/cpp-dispatch-benchmark/results/curve_ascii.txt`

- [ ] **Step 1: Write a tiny ASCII chart generator (no raster images; matches Part 6 style)**

`scripts/ascii_curve.py`:
```python
#!/usr/bin/env python3
"""Render ns-vs-offset CSVs as an ASCII chart for the blog post."""
import csv, sys

def load(path):
    with open(path) as f:
        return [(int(r["offset"]), float(r["ns_per_call"])) for r in csv.DictReader(f)]

def render(series):  # series: list of (label, [(off, ns)])
    allns = [ns for _, pts in series for _, ns in pts]
    lo, hi = min(allns), max(allns)
    width = 40
    out = []
    for label, pts in series:
        out.append(label)
        for off, ns in pts:
            n = 0 if hi == lo else round((ns - lo) / (hi - lo) * width)
            out.append(f"  off {off:>2}  {ns:5.2f} | {'#' * n}")
        out.append("")
    return "\n".join(out)

if __name__ == "__main__":
    series = [(p.split("offset_sweep_")[-1].split(".csv")[0], load(p)) for p in sys.argv[1:]]
    print(render(series))
```

- [ ] **Step 2: Generate the chart**

Run:
```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
python3 scripts/ascii_curve.py results/offset_sweep_gcc15.csv results/offset_sweep_clang.csv | tee results/curve_ascii.txt
```
Expected: an ASCII chart with one bar per offset for each compiler. Eyeball that the cliff is visible.

- [ ] **Step 3: Commit**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git add scripts/ascii_curve.py results/curve_ascii.txt
git commit -m "bench: ASCII offset-curve renderer + generated chart (Part 7)"
```

---

## Task 9: Write the blog post

**Files:**
- Create: `/home/sgambhir/tmp/shubhankar-gambhir.github.io/_posts/2026-06-16-the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.md`

(Adjust the date in the filename to the actual publish date before publishing.)

- [ ] **Step 1: Write the post front matter (no `image:` yet -- the generator adds it in Task 10)**

```markdown
---
title: "The Alignment Cliff: Why Moving a Function One Byte Can Cost You 20%"
date: 2026-06-16
categories: [C++, Performance]
tags: [alignment, dsb, micro-op-cache, skylake, perf-stat, benchmarks, gcc, clang, microbenchmark]
description: >-
  The same function, byte-for-byte identical, runs 20% slower depending only on
  where the linker put it. A minimal single-function benchmark, the full Skylake
  DSB and micro-op-cache mechanics, and a GCC/Clang/MSVC alignment flag reference.
mermaid: true
---
```

- [ ] **Step 2: Write the body following the spec outline and house style**

Sections (populate every table/number from the committed CSVs and perf/asm outputs; do NOT invent figures):
1. Hook + the offset curve (from `results/curve_ascii.txt` + a markdown table from `offset_sweep_gcc15.csv`).
2. The experiment: the two-TU harness, the `.skip` NOP-shim method, `-falign-functions=1`, and the nm-verified offsets. Link the companion files.
3. Reading the curve: where the cliffs land (32B/64B boundaries).
4. The Skylake front-end in full: L1i (64B lines) -> IFU 16B/cycle -> predecode -> MITE vs DSB (32 sets x 8 ways x 6 uops = 1536; 32-byte windows; 3 ways/window; up to 6 uops/cycle) -> IDQ -> LSD. Cite Intel Optimization Reference Manual, Agner Fog (Skylake), easyperf, WikiChip.
5. perf counters confirm DSB->MITE (markdown table from the two `perf_gcc15_*` files).
6. GCC vs Clang: the two curves side by side (markdown table merging both CSVs) + the `work()` asm for each (AT&T, annotated, with a Compiler Explorer link built from `sweep_work.c` + the exact flags).
7. Cross-compiler flag reference: a GCC/Clang/MSVC table from `crosscompiler_align_notes.md`.
8. Production guidance + benchmarking hygiene (brief; reference Part 6, do not rehash).
9. Footer: companion repo link; methodology block (hardware, exact GCC 15.2.0 + Clang version, flags, 100M iters, 1M warmup, best of 3, `taskset -c 0`, perf version); series nav (Series start: Part 1; Previously: Part 6 via `{% post_url %}`).

House-style checks to apply WHILE writing:
- No em dashes (use spaced `--` or reword); no emojis.
- Every assembly snippet is AT&T with a Compiler Explorer link.
- Pinned compiler versions everywhere.
- 2500-3000 words; vary sentence structure; let data speak (no "this is significant because" after tables).

- [ ] **Step 3: Verify house-style mechanically**

Run:
```bash
cd /home/sgambhir/tmp/shubhankar-gambhir.github.io
f=_posts/2026-06-16-the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.md
grep -n "—" "$f" && echo "EM DASH FOUND -- fix" || echo "no em dashes OK"
grep -cE "post_url" "$f"      # expect >=2 (series nav)
grep -ciE "xeon gold 6130|100M|best of 3|taskset" "$f"  # methodology present
wc -w "$f"                     # target 2500-3000
```
Expected: no em dashes; >=2 `post_url`; methodology keywords present; word count in range.

- [ ] **Step 4: Commit the draft**

```bash
cd /home/sgambhir/tmp/shubhankar-gambhir.github.io
git add _posts/2026-06-16-the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.md
git commit -m "draft: Part 7 -- the alignment cliff (DSB/micro-op-cache deep-dive)"
```

---

## Task 10: Generate the OG social card

- [ ] **Step 1: Run the generator (creates the card + adds og-only `image:` front matter)**

Run:
```bash
cd /home/sgambhir/tmp/shubhankar-gambhir.github.io
python3 tools/generate_og_cards.py
```
Expected: prints `card + front-matter   the-alignment-cliff-...` for the new post; existing posts show `front-matter already present`.

- [ ] **Step 2: Visually verify the new card**

Read the generated PNG `assets/img/og/the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.png` and confirm the title fits and the layout is clean (no footer collision).

- [ ] **Step 3: Commit**

```bash
cd /home/sgambhir/tmp/shubhankar-gambhir.github.io
git add assets/img/og/the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.png \
        _posts/2026-06-16-the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.md
git commit -m "Part 7: add OG social card + og-only image front matter"
```

---

## Task 11: Review (8-dimension audit)

- [ ] **Step 1: Run /review-blog on the new post**

Invoke the `review-blog` skill against the new post. Address every flagged issue (accuracy, methodology completeness, links, AI tells, em dashes, pinned versions, footer). Re-commit fixes:
```bash
cd /home/sgambhir/tmp/shubhankar-gambhir.github.io
git add -A && git commit -m "Part 7: address /review-blog findings"
```
Expected: review passes with no high-severity findings.

- [ ] **Step 2: Verification-before-completion pass**

Invoke `superpowers:verification-before-completion`: confirm every claim in the post traces to a committed result file; confirm internal links resolve; confirm the companion repo has all referenced sources.

---

## Task 12: Publish

- [ ] **Step 1: Push the companion repo (sources + data are public-referenced by the post)**

```bash
cd /home/sgambhir/tmp/cpp-dispatch-benchmark
git push origin HEAD
```

- [ ] **Step 2: CONFIRM with the user before the blog deploy (public, irreversible)**

Ask the user to approve the push. On approval:
```bash
cd /home/sgambhir/tmp/shubhankar-gambhir.github.io
git push origin main
```

- [ ] **Step 3: Watch the Actions build + verify live**

```bash
curl -s "https://api.github.com/repos/Shubhankar-Gambhir/Shubhankar-Gambhir.github.io/actions/runs?per_page=1" \
 | python3 -c "import sys,json;r=json.load(sys.stdin)['workflow_runs'][0];print(r['head_sha'][:7],r['status'],r['conclusion'])"
# after success:
slug=the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache
curl -sL "https://shubhankar-gambhir.github.io/posts/${slug}/" | grep -oE 'og:image[^>]+|Alignment Cliff' | head
```
Expected: build `completed success`; live page contains the title and per-post `og:image`.

- [ ] **Step 4: Update CLAUDE.md series index (local-only, gitignored)**

Add the new post to the "Published Posts" list in `/home/sgambhir/tmp/shubhankar-gambhir.github.io/CLAUDE.md` (it is gitignored, so just edit on disk).

---

## Self-Review (completed during planning)

**Spec coverage:** (1) minimal single-function benchmark -> Tasks 1,2,5. (2) full Skylake DSB mechanics -> Task 9 section 4 (+ perf proof Task 6). (3) GCC/Clang/MSVC flag reference -> Task 7 + Task 9 section 7. Standalone Part 7 positioning, OG card, companion-repo reproducibility, methodology/footer -> Tasks 9,10. Both compilers measured -> Tasks 4,5. perf event fallback risk -> Task 3 + Task 6 Step 1. Clang-install fallback -> Task 4 Step 2. No-cliff fallback -> Task 5 Step 4.

**Placeholder scan:** `<GOOD>/<BAD>` in Task 6 and the version string in Task 4 are runtime-resolved values explicitly produced by named earlier steps (not vague TODOs). Post numbers come from committed CSVs, not invented. No "add error handling"/"similar to" placeholders.

**Type/name consistency:** symbol `work` (C linkage) used consistently across harness decl, sweep source, awk label match (`^work:`), nm filter (`$3=="work"`), and objdump (`<work>:`). TAG names (`gcc15`, `clang`) consistent across runner, perf script, chart, and post. CSV columns (`offset,ns_per_call`) consistent across runner, perf good/bad selection, and chart loader.
