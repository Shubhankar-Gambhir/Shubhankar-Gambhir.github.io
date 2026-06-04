# Blog: shubhankar-gambhir.github.io

## Overview

Personal technical blog (Jekyll + Chirpy theme, GitHub Pages). Primary content: C++ dispatch performance series. Companion repo: `~/tmp/cpp-dispatch-benchmark/`.

## Repo Layout

```
_posts/           Blog posts (YYYY-MM-DD-slug.md)
_tabs/            Static pages (about, speaking, archives)
assets/           Images, CSS overrides
_config.yml       Jekyll config
.claude/skills/   Blog-specific skills
```

## Blog Post Workflow

1. Write post in `_posts/YYYY-MM-DD-slug.md`
2. Run `/review-blog` before publishing (8-dimension audit)
3. `git add`, `git commit`, `git push origin main`
4. GitHub Actions deploys in ~2 minutes
5. Verify at https://shubhankar-gambhir.github.io

## Superpowers Integration

Use these superpowers skills for blog work:

- **`superpowers:brainstorming`**: Before writing any new blog post. Design the outline, audience, key findings, and structure before touching markdown.
- **`superpowers:writing-plans`**: When a post requires pre-work (benchmarks, compiler installs, data collection). Create a task list before executing.
- **`superpowers:verification-before-completion`**: Before claiming a post is ready. Verify the Jekyll build, check links, confirm the page renders.
- **`/review-blog`** (local skill): Before posting to r/cpp, HN, or any public forum. Runs the 8-dimension audit.

## Writing Conventions

### Must Follow

- **No em dashes.** Use " -- " (spaced double hyphen) or reword. Em dashes signal AI-generated text.
- **No emojis** unless explicitly requested.
- **Pin compiler versions.** Never say "GCC" without a version. Always specify exact version (e.g., GCC 15.2.0).
- **Pin stdlib versions.** Link to source with release tags (e.g., `hb=releases/gcc-12.4.0`), never `main`.
- **Jekyll `post_url` for internal links.** Use `{% post_url YYYY-MM-DD-slug %}`, never hardcoded URLs.
- **AT&T assembly syntax** (GCC default). Annotate every instruction.
- **Benchmark methodology in every post.** Hardware, compiler, flags, iterations, warmup, runs.
- **Alignment flags.** Always use `-falign-functions=64 -falign-loops=64` per Part 4's findings.
- **Footer required.** Companion repo link, hardware specs, previous/next post links.

### Style

- Voice: conversational, second-person, technical but accessible.
- Opening: one-sentence hook (question or surprising statement).
- Length: 2500-3000 words per post.
- Tables: markdown tables for benchmark data.
- Code: fenced blocks with language identifier.
- Compiler Explorer links for every assembly snippet.

### Avoiding AI Tells

- Vary sentence structure. Don't use the same pattern for consecutive paragraphs.
- Avoid meta-commentary: "The irony:", "Counterintuitively,", "Interestingly,", "It's worth noting".
- Don't bold-number conclusions (`**1.** ... **2.** ...`). Use flowing prose.
- Mix punctuation: semicolons, periods, commas, not just " -- " everywhere.
- Let data speak. Don't add "this is significant because" after a table.

## Dispatch Blog Series

### Published Posts (in order)

1. `2026-05-07-four-ways-to-dispatch-a-runtime-selected-strategy-in-cpp.md`
2. `2026-05-12-lazy-resolution-resolve-once-dispatch-forever.md`
3. `2026-05-19-why-std-visit-may-be-slower-than-a-vtable.md`
4. `2026-05-25-your-stdlib-implementation-matters-more-than-the-dispatch-pattern.md`
5. `2026-06-02-when-dispatch-mechanism-choice-stops-mattering.md`

### Planned

6. Alignment artifacts post (cache-line boundary effects on benchmarks). Teased in Part 4 as "a future post."

### Hardware

- Intel Xeon Gold 6130 @ 2.10 GHz (xeongoldgc01.azulsystems.com)
- AVX-512 capable (benchmarks use `-march=skylake-avx512`)

### Compiler Envs

GCC versions via conda-forge micromamba at `~/utils/mamba/envs/`:
- `gcc9` through `gcc15`
- Binary pattern: `~/utils/mamba/envs/gccXX/bin/x86_64-conda-linux-gnu-g++`
- GCC 15 binaries need `-static` on the Xeon (glibc 2.28 vs 2.34)

### Benchmark Flags

```
-std=c++17 -O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64
```

### Distribution Strategy

- Reddit r/cpp: Monday/Tuesday, batch multiple posts, lead with nuance not surprise
- HN: standalone-interesting posts, not series installments
- Don't post every part; batch 2-3 posts per submission
- Have defenses ready for "why not latest compiler?" and "these barriers are too trivial" objections

## Analytics

- GoatCounter: https://shubhankar-gambhir.goatcounter.com
- Newsletter: Buttondown (sgambhir@gmail.com)

## Git

- Remote: `git@github.com:Shubhankar-Gambhir/Shubhankar-Gambhir.github.io.git`
- Branch: `main`
- Deploy: GitHub Actions on push to main
