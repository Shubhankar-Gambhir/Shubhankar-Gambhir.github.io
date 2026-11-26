# Reading-Time Cap: Trim the Middle, Split the Giants

Date: 2026-11-26. Status: draft for author approval. Builder check done; its five questions are answered below (two by the author, three as Claude's calls, marked).

## Intent

The author's reason: "people in general don't want to read long posts." The goal is that no single post feels long, so the target is a per-post cap, not just a better average. Today the 14 published posts average 15.1 minutes on Chirpy's own counter (median 13.5), and nine of them are over 12 minutes. The author asked for an average of 10 to 12 minutes.

**Success:** every post shows "12 min" or less on the live site, and the average lands at about 10.5 to 11.

## How Chirpy counts

`_includes/read-time.html` in jekyll-theme-chirpy computes `strip_html` word count divided by 180, rounded down. Code blocks, tables and the footer all count. "12 min" means fewer than 2,340 counted words. Measured on *Pick Two*, counted words are about 0.93 times the raw markdown body word count, so the working budget is **about 2,450 raw body words or fewer**. The only check that counts is the number Chirpy prints in a local production build.

## Goals

- Every post at 12 minutes or less.
- The three longest posts are split at their existing section boundaries, so no measured data is deleted.
- The six mid-length posts (13 to 15 minutes) are trimmed down to 12 minutes, cutting repetition, side trips and over-long quotes first.
- Every internal link, `{% post_url %}` and anchor still resolves after the change, and every "Previously/Next" chain still reads end to end.
- Future posts are written to the new length (CLAUDE.md and the HotSpot style lint updated).

## Non-goals

- No new benchmarks, no re-measurement, no changes to findings or numbers.
- No changes to the five posts already at 12 minutes or less, except for link and footer fixes.
- No changes to companion repos. None of them link to blog post URLs (checked).
- No reposting to r/cpp or HN as part of this work.

## Anti-goals (a failure even if every post shows 12 min)

- Deleting measured data or the evidence behind a claim to hit the number.
- A split half that can't be read on its own: no context, a dangling "as we saw above", or a closer that only makes sense after the other half.
- Breaking a link from another post, or a URL someone may have shared. The original URL must still show the start of the original post.
- Invented anecdotes or human details in new intros and closers (CLAUDE.md rule). Where a human line belongs, leave an `<!-- AUTHOR: -->` prompt and do not publish that post until it's answered.
- New AI tells in the new prose: TOC sentences, recap lists, formulaic "In Part 1 we saw..." openers repeated across the halves.

## Approaches considered

| | A. Trim the middle, split the giants | B. Trim everything | C. Split only |
|---|---|---|---|
| What | Trim the six 13-15 min posts to 12; split -fno-rtti, queues Part 2 and Part 3 at section boundaries | Cut all nine to 12 | Split the big three, no trims |
| Result | 20 posts, avg about 10.6, none above 12 | 14 posts, avg about 11.8 | 18 posts, avg about 11.8, six posts still 13-15 |
| Cost | Trims of 1-3 min per mid post; big posts keep their content | -fno-rtti loses about 60%, Part 2 about 45% (measured data) | Misses the goal: 15-min posts stay long |

**Picked: A** (author's choice, 2026-11-26). It's the only option that caps every post without deleting benchmark data.

## The splits

Raw body word counts are measured from the current files; minutes are estimates until the build confirms them. In each split, **the first half keeps the original filename, date and URL**, so inbound links and shared URLs still land on the start of the post. Each new half gets a short intro written for a reader arriving cold, and a closer of its own.

### -fno-rtti (`2026-08-26-the-null-slot-what-fno-rtti-actually-removes`, 5,720 words, 30 min) into four posts

| # | Sections | Raw words | Est. | URL / date |
|---|---|---|---|---|
| 1 | intro, What the Flag Is, Why Codebases Turn It Off, and Who Should | 1,509 + closer + footer | ~9-10 min | original URL, 2026-08-26 |
| 2 | Impact: Size, Load Time, Build Time, Speed | 1,381 + intro | ~8 min | new, 2026-08-27 |
| 3 | What Changed Across Compilers and Versions, What's Coming | 1,462 + intro | ~8 min | new, 2026-08-28 |
| 4 | OpenJDK, and Everyone Else, A Design Flag Wearing a Size Flag's Clothes | 1,322 + intro | ~8 min | new, 2026-08-29 |

Four posts rather than three, because a three-way cut at section boundaries leaves one post at 14 to 15 minutes, which would need content deleted. The existing closer, "A Design Flag Wearing a Size Flag's Clothes", is the thesis of the whole piece and stays at the end of post 4. Post 1 gets a short closer of its own (builder writes; see below).

### Queues Part 2 (`2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring`, 4,411 words, 22 min) into three posts

Every post carries the full footer CLAUDE.md requires (Part 2's is 192 words), so the first halves' budgets include one. A two-way split leaves 2a at about 14 minutes with its footer, which would mean cutting the ABA narrative this design protects. So Part 2 splits three ways (builder-check finding; Claude's call):

| # | Sections | Raw words | Est. | URL / date |
|---|---|---|---|---|
| 2a | intro, The Obvious Move, Forty Out of Forty, The Same Bug, Different Odds, Making the Pointer Change | 1,645 + closer + footer | ~10 min | original URL, 2026-08-19 |
| 2b | Removing the Mutex Made It Slower, On One Machine, Not Allocating At All | 1,174 + intro + footer | ~8 min | new, 2026-08-20 |
| 2c | The Ring, A Correction to Part 1, Where That Leaves the Pool | 1,542 + intro + footer | ~10 min | new, 2026-08-21 |

The `#the-ring` anchor moves to 2c. One in-repo link uses it (Part 3 line 214) and is repointed to 2c. Shared links to `Part2#the-ring` from outside will land on 2a's top, which is acceptable. The existing "Update, November 2026" note stays with whichever section it annotates. Each post's footer keeps only the methodology and hardware lines its own data needs.

### Queues Part 3 (`2026-10-27-the-copy-and-the-backlog-...`, 3,450 words, 18 min) into two posts

| # | Sections | Raw words | Est. | URL / date |
|---|---|---|---|---|
| 3a | intro, A Correction to Part 2, The Setup, The First Sweep, Where the Copy Happens | 1,904 + closer + footer | ~11 min | original URL, 2026-10-27 |
| 3b | What the Copy Compiles To, Falling Behind, The Ring Does Not Care, Two Layers | 1,510 + intro | ~8.5 min | new, 2026-10-28 |

*Pick Two*'s opener ("My last queue post ended with an unbounded queue growing until the kernel killed it") must link to **3b**, because the OOM ending and the "Two Layers" closer move there. The intro hook ("reached 64.6 GB...") stays in 3a; the `own-oom` source entry in *Pick Two*'s sidecar follows whichever post holds that sentence.

### Series numbering

The queue series grows from three posts to six. **Renumber sequentially** (Part 1 to 6 = current 1, 2a, 2b, 2c, 3a, 3b) and update every "Part N" mention that refers to the queue series, including headings like "A Correction to Part 1" and "A Correction to Part 2". Post titles carry no part numbers, so only prose and footers change. Careful: the dispatch series also uses "Part 1" to "Part 7", so every "Part N" hit must be read in context before changing it. The fno-rtti posts get a light series label in their intros (builder's wording); they aren't currently numbered.

### First halves: titles, openings, descriptions (Claude's calls)

- **Retitling is allowed; URLs never change.** -fno-rtti post 1 becomes "The Null Slot: What -fno-rtti Actually Removes" (it matches its slug). Part 2a drops "and a Bounded Ring". Part 3 becomes two posts named "The Copy..." (3a, keeps URL) and "The Backlog..." (3b); the builder finishes both titles. Each post's `description:` is rewritten to summarize only that post. Link texts across the site follow the new titles.
- **Openings stay; their promises get fixed.** The first paragraph of each original post stays as close to verbatim as possible, because shared URLs still land there. Any sentence promising content that moved (Part 3a's "one of the queues in this post reached 64.6 GB", whose payoff moves to 3b; -fno-rtti's "OpenJDK builds `libjvm.so` with it", now in post 4; the "Revised 1 October 2026" note, whose GCC 9-15 data moves to post 3; Part 2a's "bounded ring buffer" promise, now 2c) is reworded to point forward to the post that delivers it, with a link.
- **Citation side effect:** Part 3a's hook is quoted verbatim as `own-oom` in `docs/hotspot-series/pick-two-...sources.yml`. If it's reworded, update that entry (text and URL) and rerun `verify_sources.py`.
- **No split notice** (author's choice). The posts read as an ordinary series; only footers and intros connect them. No "this post was split" note anywhere.

## The trims

Target: each at 12 minutes or less in the build, about 2,450 raw words or fewer.

| Post | Now | Cut | Where to look first |
|---|---|---|---|
| Queues Part 1, `a-deep-dive-into-producer-consumer-queues` | 2,904 (15 min) | ~460 | "The Pooling Detour" (820 words) |
| Dispatch Part 5, `when-dispatch-mechanism-choice-stops-mattering` | 2,967 (15) | ~520 | "The Full Picture" (891) |
| *Pick Two* | 2,709 (14) | ~260 | "Stall, as an Option", "Writing Yours Down" |
| Alignment Cliff, `the-alignment-cliff-...` | 2,677 (14) | ~230 | "The Geometry" (607) |
| Lock cost, `what-does-a-lock-actually-cost-...` | 2,563 (13) | ~120 | "relaxed vs seq_cst" (524) |
| std::visit, `why-std-visit-may-be-slower-than-a-vtable` | 2,530 (13) | ~90 | "Inside the Stdlib" (1,103) |

What to cut, in order: restated conclusions, repeated setup, side trips, long quotes that can be shortened without changing their meaning, then prose that only narrates a table. Never cut: numbers in tables, methodology, Compiler Explorer links, corrections, the "dead end / wrong first guess" passages (CLAUDE.md "tell it in the order it happened").

**Part 1's "Pooling Detour" constraint:** Part 2b's "Where That Leaves the Pool" builds on it, so trim the detour's prose but keep the measured pool numbers and the claims 2b refers back to.

## Links, footers, cards

- **Footers follow series order only** (Claude's call): Previously and Next point to neighbours in the same series; the home page already lists posts by date. Queue series: 1 -> 2 -> 3 -> 4 -> 5 -> 6; Part 6 has no Next. fno-rtti: 1 -> 2 -> 3 -> 4; post 1 has no Previously and post 4 has no Next. This replaces the current mixed footers (Part 2's Next -> fno-rtti, fno-rtti's Previously -> Part 2). *Pick Two* keeps no Previously. Each footer's link text must match the target's current title.
- **Inbound links to update:** CppCon post lines 63 and 106 (to Part 2), Part 1 line 248 (Next), fno-rtti line 401 (Previously, removed under the series rule), Part 3 lines 19, 214, 230, *Pick Two* line 18 (to 3b, where the OOM ending now lives). Each goes to whichever post now holds the content it refers to.
- **Newsletter:** Buttondown has no RSS automation (author, 2026-11-26), so the new back-dated posts email no one. Batches can go live as soon as they pass verification and the author has seen them.
- **Social cards:** run `tools/generate_og_cards.py`, which adds cards and front matter for the new posts and re-renders existing cards. Any original post whose title changes gets a re-rendered card; check that no other card changes.
- **Redirects:** not needed, because every original URL still exists. `jekyll-redirect-from` is installed if a later change needs it.

## Length guidance for future posts

- CLAUDE.md "Length: 2500-3000 words per post" becomes "12 minutes or less on Chirpy's counter: about 2,300 words including code and tables (roughly 1,800 to 2,200 words of prose)".
- `tools/hotspot_series/style_lint.py` `MIN_WORDS, MAX_WORDS = 2500, 3000` becomes `1500, 2200` (prose words). This also applies to *Two Commits* (2,895 prose words), which will need trimming before it publishes; that's a separate task.
- `/review-blog`'s Dimension 7 "significantly thinner than the rest" check now compares against the new length.

## Verification (every changed post)

1. Local production build passes, and the post shows 12 min or less (read from the built HTML, not estimated).
2. Every `{% post_url %}` resolves (the build fails if not) and every `#anchor` link points at a heading that exists in its target.
3. Lint for the house rules: no em dashes or " -- " in prose, no emojis, `nasm` fences not `asm`.
4. A light `/review-blog` pass on each post's new or changed prose (intros, closers, joins): AI tells, standalone readability, links, footer.
5. Compiler Explorer links, tables and numbers are byte-identical to before, unless a trim deliberately removed prose around them (diff check).
6. After deploy: the live page shows the expected reading time, and the original URLs still load.

## Left to the builder

- Exact cut lines inside a section when trimming, within the rules above.
- Wording of the new intros, closers and series labels, in the series voice, with no invented anecdotes.
- Titles and slugs for the new halves (descriptive, in the style of the existing titles; slug = title).
- Batching: by series (queues, fno-rtti, the six trims) with one commit per post. Each batch is built, checked and shown to the author before it goes live.
