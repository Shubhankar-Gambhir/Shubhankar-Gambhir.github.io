# Reading-Time Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every post on the blog shows "12 min" or less on Chirpy's reading-time counter, by splitting the three longest posts at section boundaries and trimming six mid-length ones.

**Architecture:** One checker script (`tools/post_checks.py`) is the test harness for every content task: it reads reading time from a local production build, checks anchors, prose rules, footers and data preservation. Content tasks then split or trim one post each, run the checker, and commit. Posts go live in batches (queue series, fno-rtti, trims), each after the author has seen it.

**Tech Stack:** Jekyll + Chirpy theme, Python 3.10 (stdlib + PyYAML), pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-reading-time-cap-design.md` (read it before any task; it holds the split tables, section lists and dates).

## Global Constraints

- Cap: Chirpy prints `floor(strip_html words / 180)` minutes; every touched post must show **12 min or less** in the built HTML. Working budget about 2,450 raw markdown body words.
- In every split, the first half keeps the original filename, date and URL. New halves: Part 2b `2026-08-20`, Part 2c `2026-08-21`, fno-rtti 2/3/4 `2026-08-27`/`08-28`/`08-29`, Part 3b `2026-10-28`.
- No measured data deleted: table rows, numbers, methodology, Compiler Explorer links, corrections and dead-end passages survive (diff-checked).
- No invented anecdotes. Where a human line is needed, leave `<!-- AUTHOR: ... -->` and do not publish that post until the author answers.
- House rules (CLAUDE.md): no em dashes, no " -- " in prose (allowed only in Mermaid, assembly comments, table N/A cells), no emojis, ```` ```nasm ```` never ```` ```asm ````, internal links only via `{% post_url %}`.
- No split notice anywhere (author's choice). Footers follow **series order only** (spec, "Links, footers, cards").
- Every post keeps a full footer (companion repo, hardware, methodology lines its own data needs, Previously/Next).
- Commits: no `Co-Authored-By: Claude` line (author's rule for this repo). One commit per post.
- Nothing is pushed until the author has seen the batch (Tasks 5, 6 and 13 end with a show-and-push step).
- Build: `export PATH=~/utils/mamba/envs/ruby/bin:$PATH:~/utils/mamba/envs/gcc14/bin && JEKYLL_ENV=production bundle exec jekyll build -d ~/tmp/blog-check`
- Tests: `python3 -m pytest -p no:anyio -q tools/tests` (the anyio plugin is broken on this host).
- Baseline for preservation checks: commit `b0a3797` (originals before this work).

## Review Focus

1. **A split half that references the other half as if it were on the same page** ("above", "below", "earlier in this post", "as we saw"). Expected: every such phrase is reworded or turned into a link. Pinned by `cold_refs` warnings, which each split task must resolve to zero.
2. **"Part N" renumbering touching the dispatch series**, which also uses Part 1 to 7. Expected: dispatch posts' "Part N" text is unchanged. Pinned by Task 5 Step 4.
3. **A fenced block or table cut in half at a split point.** Expected: the build still renders every code block and table whole. Pinned by `prose_violations` (unbalanced fences) in every split task.
4. **A footer whose link text no longer matches the target's (re)title.** Expected: every Previously/Next text equals the target's current `title:`. Pinned by `footer_violations`.
5. **A new post missing `description:` or `image:` front matter**, which makes the home page card and the social preview blank. Expected: both are present on every post. Pinned by `front_matter_violations`.

---

### Task 1: Post checker

**Files:**
- Create: `tools/post_checks.py`
- Create: `tools/series.yml`
- Test: `tools/tests/test_post_checks.py`

**Interfaces:**
- Produces (all in `tools/post_checks.py`; `md` is a whole post file's text):
  - `read_minutes(html: str) -> int | None`: the N from Chirpy's `<em>N min</em>`, first match.
  - `prose_violations(md: str) -> list[str]`: em dash or " -- " in prose (skips fenced blocks, Mermaid, lines starting `|`); emoji (Unicode ranges U+1F300-U+1FAFF, U+2600-U+27BF); a ```` ```asm ```` fence; an odd number of ```` ``` ```` fences; a hard-coded `shubhankar-gambhir.github.io/posts` link.
  - `cold_refs(md: str) -> list[str]`: prose lines (outside fences) containing `above`, `below`, `earlier in this post`, `later in this post`, `as we saw` (case-insensitive, whole words).
  - `front_matter_violations(md: str) -> list[str]`: missing `title:`, `date:`, `description:` or `image:`.
  - `footer_violations(posts_dir: Path, series: dict[str, list[str]]) -> list[str]`: for each series (ordered list of post basenames without `.md`), the first post has no `**Previously:**` line, the last has no `**Next:**` line, and every other post's Previously/Next links use `{% post_url <neighbour> %}` with link text equal to the neighbour's `title:`.
  - `missing_items(old_md: str, new_mds: list[str], allow: set[str] = frozenset()) -> list[str]`: every `godbolt.org` URL and every table row (line starting `|`, not a `|---` separator) in `old_md` that appears in none of `new_mds` and is not in `allow`.
  - `broken_anchors(site_dir: Path) -> list[str]`: every `href="/posts/<slug>/#<id>"` or `href="#<id>"` in `site_dir/posts/*/index.html` whose `<id>` has no `id="<id>"` in the target page.
  - CLI: `python3 tools/post_checks.py --site DIR [--max-minutes 12] [--series tools/series.yml] FILES...`. It prints `FAIL <file>: <msg>` for violations and `WARN <file>: <msg>` for cold refs, then `minutes <slug> N` for each file. It exits 1 on any FAIL or on any minutes > max.
- `tools/series.yml`: `queues:` and `fno-rtti:` lists of post basenames in series order. Initially `queues: [2026-07-07-..., 2026-08-19-..., 2026-10-27-...]` and `fno-rtti: [2026-08-26-...]`. Tasks 2, 3 and 7 update it.

- [ ] **Step 1: Write the failing tests** in `tools/tests/test_post_checks.py`, with tiny inline fixtures:
  - `test_read_minutes`: `read_minutes('<span><em>14 min</em> read</span>') == 14`; returns `None` when absent.
  - `test_prose_violations_flags`: em dash in prose flagged; `" -- "` inside a ```` ```mermaid ```` block and inside a `|` table row not flagged; ```` ```asm ```` flagged; three ```` ``` ```` fences flagged as unbalanced.
  - `test_cold_refs`: `"As shown above, x"` is reported; `"aboveground"` and an "above" inside a fence are not.
  - `test_front_matter`: a post without `image:` returns exactly one violation naming `image`.
  - `test_footer_chain` (tmp dir with 3 posts A, B, C): correct chain gives `[]`; wrong link text on B's Next, or a Previously on A, each gives exactly one violation.
  - `test_missing_items`: a godbolt URL and a table row moved into the second of two new files give `[]`; a dropped row is reported; a dropped row listed in `allow` is not.
  - `test_broken_anchors` (tmp site with two pages): `href="/posts/b/#the-ring"` passes when page b has `id="the-ring"` and fails when it doesn't.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m pytest -p no:anyio -q tools/tests/test_post_checks.py`
Expected: FAIL (`ModuleNotFoundError: post_checks`). Import the tool via `sys.path` the same way `tools/hotspot_series/tests` do.

- [ ] **Step 3: Implement `tools/post_checks.py`** with the interfaces above (stdlib `re`, `html`, `argparse`, `pathlib`; `yaml` for `--series`). Also create `tools/series.yml` with the initial lists.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python3 -m pytest -p no:anyio -q tools/tests/test_post_checks.py`
Expected: all pass.

- [ ] **Step 5: Baseline run on the repo.** Build, then run `python3 tools/post_checks.py --site ~/tmp/blog-check _posts/*.md`. Expected: `minutes` lines reproduce the spec's numbers (fno-rtti 30, Part 2 22, Part 3 18, Part 1 15, dispatch Part 5 15, *Pick Two* 14, Alignment Cliff 14, std::visit 13, lock cost 13). Non-zero exit is expected here: the current footers break the series rule. Record any other FAIL it prints in the commit message; those are existing defects for the content tasks to fix.

- [ ] **Step 6: Commit** `tools/post_checks.py tools/series.yml tools/tests/test_post_checks.py`, message `post_checks: reading-time, anchor, footer and preservation checker`.

---

### Task 2: Split queues Part 2 into three posts

**Files:**
- Modify: `_posts/2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring.md` (becomes 2a)
- Create: `_posts/2026-08-20-<2b-slug>.md`, `_posts/2026-08-21-<2c-slug>.md`
- Modify: `tools/series.yml`

**Interfaces:**
- Consumes: Task 1's CLI and `missing_items`.
- Produces: final 2b and 2c basenames in `tools/series.yml` (Tasks 3 to 5 link to them). The `#the-ring` heading lives in 2c.

- [ ] **Step 1: Split at the spec's boundaries.** 2a = intro through "Making the Pointer Change"; 2b = "Removing the Mutex Made It Slower, On One Machine" + "Not Allocating At All"; 2c = "The Ring" + "A Correction to Part 1" + "Where That Leaves the Pool". 2a's title drops "and a Bounded Ring". 2b and 2c get descriptive titles in the series style, with slug = title. Each post gets its own `description:`, a short intro (2b, 2c) or closer (2a), and a footer that keeps only the hardware and methodology lines its data needs. The ABA-rate lines go with 2a. Reword 2a's "bounded ring buffer" promise to point forward to 2c with a `post_url` link. The "Update, November 2026" note stays with the section it annotates.
- [ ] **Step 2: Preservation check.** In a short Python snippet, call `missing_items(<b0a3797 version of Part 2>, [2a, 2b, 2c])`. Expected: `[]`.
- [ ] **Step 3: Add 2b and 2c to `tools/series.yml`** (queues order: Part 1, 2a, 2b, 2c, Part 3). Leave footers to Task 5.
- [ ] **Step 4: Build and check.** `python3 tools/post_checks.py --site ~/tmp/blog-check <2a> <2b> <2c>`. Expected: minutes at 12 or less for all three (spec estimates about 10/8/10); zero `prose_violations`, `front_matter_violations` (2b and 2c `image:` may be absent until Task 5's card run, so that is the one FAIL allowed here); every `cold_refs` WARN fixed by rewording or linking, then re-run until no WARN remains.
- [ ] **Step 5: Commit** the three posts and `tools/series.yml`, message `Split queues Part 2 into three posts (ABA, pool, ring)`.

---

### Task 3: Split queues Part 3 into two posts

**Files:**
- Modify: `_posts/2026-10-27-the-copy-and-the-backlog-what-a-bigger-payload-does-to-a-concurrent-queue.md` (becomes 3a)
- Create: `_posts/2026-10-28-<3b-slug>.md`
- Modify: `_posts/2026-11-12-pick-two-what-your-queue-does-when-the-consumer-falls-behind.md:18`
- Modify: `docs/hotspot-series/pick-two-what-your-queue-does-when-the-consumer-falls-behind.sources.yml` (`own-oom` entry)
- Modify: `tools/series.yml`

**Interfaces:**
- Consumes: 2c's basename (Part 3 line 214's `#the-ring` link is repointed to it).
- Produces: 3b basename in `tools/series.yml`.

- [ ] **Step 1: Split.** 3a = intro, "A Correction to Part 2", "The Setup", "The First Sweep", "Where the Copy Happens", titled "The Copy..."; 3b = "What the Copy Compiles To", "Falling Behind", "The Ring Does Not Care", "Two Layers", titled "The Backlog...". The builder finishes both titles. Reword 3a's OOM hook ("one of the queues in this post reached 64.6 GB...") so it points forward to 3b with a link, and reword 3b's "That is the run from the top of this post" so it stands alone. Repoint the `#the-ring` link to 2c. Give each post its own description and footer.
- [ ] **Step 2: *Pick Two* follow-ups.** Line 18's "My last queue post ended with..." link goes to 3b. Update the `own-oom` entry's `text` to the new hook sentence (verbatim) and its `url` to the post holding it.
- [ ] **Step 3: Preservation check.** `missing_items(<b0a3797 Part 3>, [3a, 3b])`. Expected: `[]`.
- [ ] **Step 4: Sources check.** `python3 tools/hotspot_series/verify_sources.py docs/hotspot-series/pick-two-*.sources.yml`. Expected: no MISSING lines. Note that `own-oom` fetches the **live** site, so it will report MISSING until Task 5 pushes. Re-run it after Task 5's deploy and record that it passes.
- [ ] **Step 5: Build and check** 3a, 3b and *Pick Two* with `post_checks.py`. Same expectations as Task 2 Step 4: minutes at 12 or less (*Pick Two* may still read 14 until Task 10).
- [ ] **Step 6: Commit**, message `Split queues Part 3 into The Copy and The Backlog; repoint Pick Two`.

---

### Task 4: Trim queues Part 1 to 12 minutes

**Files:**
- Modify: `_posts/2026-07-07-a-deep-dive-into-producer-consumer-queues.md`

**Interfaces:**
- Consumes: 2c's basename. Part 2c's "Where That Leaves the Pool" refers back to Part 1's pool numbers; those must survive.

- [ ] **Step 1: Trim about 460 raw words**, starting with "The Pooling Detour" (820 words). Follow the spec's cut order: restated conclusions, repeated setup, side trips, long quotes, prose that only narrates a table. Keep every pool number and claim that 2c refers to (grep 2c for "Part 1" and "pool" to list them first).
- [ ] **Step 2: Preservation check.** `missing_items(<b0a3797 Part 1>, [Part 1])`. Expected: `[]`.
- [ ] **Step 3: Build and check.** `post_checks.py` on Part 1. Expected: 12 min or less, no FAIL apart from footers (fixed in Task 5).
- [ ] **Step 4: Commit**, message `Trim queues Part 1 to a 12-minute read`.

---

### Task 5: Queue series renumbering, links, footers, cards; ship the queue batch

**Files:**
- Modify: queue posts 1, 2a, 2b, 2c, 3a, 3b (prose "Part N" and footers); `_posts/2026-09-29-a-week-in-aurora-cppcon-2026.md:63,106`; `_posts/2026-08-26-the-null-slot-what-fno-rtti-actually-removes.md:401`
- Create: `assets/img/og/<2b-slug>.png`, `<2c-slug>.png`, `<3b-slug>.png`; re-rendered `assets/img/og/` cards for retitled 2a and 3a

**Interfaces:**
- Consumes: `tools/series.yml` queue order from Tasks 2 and 3.

- [ ] **Step 1: Renumber.** Queue series becomes Part 1 to 6 (= 1, 2a, 2b, 2c, 3a, 3b). Update every "Part N" that refers to a queue post, reading each hit in context. That includes headings such as "A Correction to Part 1" and "A Correction to Part 2", and Part 3's "Part 2 predicted...", which may now mean 2b or 2c depending on what it cites.
- [ ] **Step 2: Footers and inbound links.** Rewrite the Previously/Next lines on all six queue posts to the series chain. Repoint the CppCon post's lines 63 and 106 to the half they mean: line 63 cites the lock-free pool, so it goes to 2b. Remove the fno-rtti footer's Previously to Part 2, per the series rule.
- [ ] **Step 3: Cards.** `python3 tools/generate_og_cards.py`. Then `git status assets/img/og` should show new PNGs for 2b, 2c and 3b and changed PNGs only for retitled posts (2a, 3a). Revert any other changed PNG. View each new card.
- [ ] **Step 4: Dispatch-series guard.** `git diff b0a3797 -- _posts/2026-05-* _posts/2026-06-02-* _posts/2026-06-10-* _posts/2026-06-15-*`. Expected: empty (no dispatch post touched yet).
- [ ] **Step 5: Full check.** Build, then `python3 tools/post_checks.py --site ~/tmp/blog-check --series tools/series.yml <all six queue posts> <CppCon> <Pick Two>`, and run `broken_anchors`. Expected: exit 0 except *Pick Two*'s minutes (Task 10), zero WARN, all queue posts at 12 or less.
- [ ] **Step 6: Commit** (one commit per post touched in Steps 1-3 is fine; at least one commit for renumbering, links and footers, and one for cards).
- [ ] **Step 7: Light review.** Run `/review-blog` on the six queue posts, limited to new or changed prose (intros, closers, joins, renumbered sentences): AI tells, standalone readability, links, footers. Fix must-fix and should-fix items, then re-run Step 5.
- [ ] **Step 8: Show the author** the six queue titles, their minutes, and the new intros and closers (paste them). Push only on their OK. After the deploy, confirm each live URL loads, the original Part 2 and Part 3 URLs show 2a and 3a, and Task 3's `verify_sources.py` now passes.

---

### Task 6: Split -fno-rtti into four posts; ship the fno-rtti batch

**Files:**
- Modify: `_posts/2026-08-26-the-null-slot-what-fno-rtti-actually-removes.md` (post 1, retitled "The Null Slot: What -fno-rtti Actually Removes")
- Create: `_posts/2026-08-27-<slug>.md`, `_posts/2026-08-28-<slug>.md`, `_posts/2026-08-29-<slug>.md`
- Modify: `tools/series.yml`, `_posts/2026-09-29-a-week-in-aurora-cppcon-2026.md` (if it links fno-rtti content that moved)
- Create: `assets/img/og/` cards for posts 2-4, plus post 1's re-rendered card

**Interfaces:**
- Consumes: Task 1's checker.

- [ ] **Step 1: Split** at the spec's boundaries. Post 1 = intro, "What the Flag Is", "Why Codebases Turn It Off, and Who Should". Post 2 = "Impact: Size, Load Time, Build Time, Speed". Post 3 = "What Changed Across Compilers and Versions", "What's Coming". Post 4 = "OpenJDK, and Everyone Else", "A Design Flag Wearing a Size Flag's Clothes". In post 1's opening, reword "I went looking because OpenJDK builds `libjvm.so` with it" and the "Revised 1 October 2026" note so they point forward to posts 4 and 3 with links. Each post gets an intro (2-4) or closer (1), a light series label in the intro, its own description, and a footer with series-order Previously/Next.
- [ ] **Step 2: Preservation check.** `missing_items(<b0a3797 fno-rtti>, [p1, p2, p3, p4])`. Expected: `[]`.
- [ ] **Step 3: Series file and cards.** Set `fno-rtti:` in `tools/series.yml` to the four basenames. Run `generate_og_cards.py`; only post 1's card should change, plus three new ones.
- [ ] **Step 4: Full check.** Build; `post_checks.py --series tools/series.yml` on the four posts and the CppCon post; `broken_anchors`. Expected: exit 0, zero WARN, each post at 12 or less (estimates 9-10 / 8 / 8 / 8).
- [ ] **Step 5: Commit**, message `Split the -fno-rtti post into four`.
- [ ] **Step 6: Light review.** `/review-blog` on the four posts' new or changed prose, as in Task 5 Step 7; fix, then re-run Step 4.
- [ ] **Step 7: Show the author** the four titles, minutes, and new intros and closers. Push on their OK, then confirm the live URLs, including the original fno-rtti URL showing post 1.

---

### Tasks 7-11: Trim one post each to 12 minutes

All six follow the same steps; each is its own task and commit, so a reviewer can reject one and approve another.

| Task | Post | Cut (raw words) | Start with |
|---|---|---|---|
| 7 | `2026-06-02-when-dispatch-mechanism-choice-stops-mattering.md` | ~520 | "The Full Picture" |
| 8 | `2026-06-15-the-alignment-cliff-code-alignment-and-the-skylake-micro-op-cache.md` | ~230 | "The Geometry" |
| 9 | `2026-06-24-what-does-a-lock-actually-cost-concurrent-counter-benchmarks.md` | ~120 | "relaxed vs seq_cst" |
| 10 | `2026-11-12-pick-two-what-your-queue-does-when-the-consumer-falls-behind.md` | ~260 | "Stall, as an Option", "Writing Yours Down" |
| 11 | `2026-05-19-why-std-visit-may-be-slower-than-a-vtable.md` | ~90 | "Inside the Stdlib" |

(Queues Part 1, the sixth trim, is Task 4, because it ships with the queue batch.)

- [ ] **Step 1: Trim** in the spec's cut order. Never cut table rows, numbers, methodology, Compiler Explorer links, corrections or dead-end passages.
- [ ] **Step 2: Preservation check.** `missing_items(<b0a3797 version>, [post])`. Expected: `[]`.
- [ ] **Step 3: Build and check** with `post_checks.py` on the post. Expected: 12 min or less, no FAIL, no new WARN. For Task 10 also run `style_lint.py` and `verify_sources.py` on *Pick Two*'s sidecar: no MISSING, and the word count fits the Task 13 range (run Task 13 first if it isn't done yet).
- [ ] **Step 4: Commit**, message `Trim <short title> to a 12-minute read`.

---

### Task 13: Length guidance for future posts; ship the trims batch

**Files:**
- Modify: `CLAUDE.md:52`
- Modify: `tools/hotspot_series/style_lint.py:28`
- Modify: `tools/hotspot_series/tests/test_style_lint.py:95-111`
- Modify: `.claude/skills/review-blog/SKILL.md` (Dimension 7)

- [ ] **Step 1: Update the test first.** In `test_style_lint.py`, the boundaries become 1499 fail, 1500 pass, 2200 pass, 2201 fail, and the exclusions case expects `prose_words=1500` (use `count(1499, extra)`).
- [ ] **Step 2: Run it and confirm it fails.** `python3 -m pytest -p no:anyio -q tools/hotspot_series/tests/test_style_lint.py`. Expected: FAIL on the new boundaries.
- [ ] **Step 3: Implement.** `MIN_WORDS, MAX_WORDS = 1500, 2200`. In CLAUDE.md, replace "Length: 2500-3000 words per post." with "Length: 12 minutes or less on Chirpy's counter: about 2,300 words including code and tables (roughly 1,800 to 2,200 words of prose)." In `/review-blog` Dimension 7, add: "Length: flag any post over 12 min in a local build (`tools/post_checks.py`)."
- [ ] **Step 4: Run the whole tool suite.** `python3 -m pytest -p no:anyio -q tools/tests tools/hotspot_series/tests`. Expected: all pass.
- [ ] **Step 5: Commit**, message `Lower length guidance to a 12-minute read`.
- [ ] **Step 6: Light review.** `/review-blog` on the five trimmed posts (Tasks 7-11), checking that the joins left by cuts read naturally; fix, then re-check minutes.
- [ ] **Step 7: Final full check and ship.** Build, then run `python3 tools/post_checks.py --site ~/tmp/blog-check --series tools/series.yml _posts/*.md` and `broken_anchors`. Expected: exit 0 and every post at 12 or less. Print the average of the `minutes` lines; expected about 10.5 to 11 over 20 posts. Show the author the trims (per post: minutes before and after, and a short list of what went). Push on their OK, then confirm the live minutes for every post with the Task 1 baseline loop against the live site.
