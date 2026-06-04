---
name: review-blog
description: Pre-publish review of blog posts across 8 dimensions -- AI tells, challengeable claims, flow, links, promises, cross-references, depth, and reader questions. Use before posting to r/cpp, HN, or any public forum.
---

# Blog Post Review

Run an 8-dimension audit on one or more blog posts before publishing or promoting. Designed for the C++ dispatch blog series but applicable to any technical blog post.

## When to Use

- Before posting to r/cpp, HN, or any public forum
- After writing a new post in the series
- After editing a published post
- When preparing a batch submission (multiple posts at once)

## Arguments

- No args: review all posts in `_posts/`
- Post filename or number: review specific post(s), e.g. `Part 5` or `2026-06-02-when-dispatch-mechanism-choice-stops-mattering.md`

## Process

### Step 1: Read All Posts

Read every `.md` file in `_posts/` (or the specified subset). You need the full text of all posts to check cross-references and consistency.

### Step 2: Run the 8-Dimension Audit

For each post, evaluate these dimensions. Report findings in a single table per dimension, not per post.

#### Dimension 1: AI-Generated Tells

Scan for patterns that signal AI-generated text:

- **Meta-commentary**: "The irony:", "Counterintuitively,", "Interestingly,", "It's worth noting that"
- **Bolded numbered lists**: `**1.** ... **2.** ... **3.**` pattern in conclusions
- **Parallel sentence structures**: enumerated items with identical `X: one thing (parenthetical). Y: another thing (parenthetical).` pattern
- **Uniform dash usage**: overuse of " -- " without variation (semicolons, periods, commas, parentheticals)
- **Perfect paragraph cadence**: every paragraph follows claim-evidence-interpretation in the same order
- **Filler qualifiers**: "It's important to note", "As mentioned earlier", "In essence"
- **Summary sentences**: trailing sentences that restate the paragraph ("This is why X matters")

#### Dimension 2: Challengeable Claims

Flag claims that r/cpp or HN commenters could attack:

- Unsourced performance numbers (cite Agner Fog, Intel manuals, or perf stat data)
- Claims about CPU microarchitecture behavior without references
- Benchmarks on old compilers without upfront disclosure
- Simplifications of hardware behavior stated as facts
- Assembly analysis that assumes specific register allocation or optimization decisions

#### Dimension 3: Flow and Understandability

For each post, assess:

- Can a reader who hasn't read the series understand the opening?
- Is any section so dense it might cause readers to bounce?
- Does the post build to its conclusion or front-load it?
- Are technical terms explained on first use?
- Is "Decoupled CRTP" explained or linked on every first mention?

#### Dimension 4: Links

Check every link in every post:

- `{% post_url %}` tags: do the slugs match actual filenames in `_posts/`?
- Hardcoded URLs to own blog: should they be `{% post_url %}` instead?
- External links (Compiler Explorer, GitHub, GCC source): are they still valid? (Use WebFetch to spot-check)
- Missing links: are there references to "Part N" or "previous post" without a hyperlink?

#### Dimension 5: Promises Not Delivered

Scan all posts for forward-looking statements:

- "In the next post, we'll..."
- "A future post will..."
- "Part N covers..."
- "Coming soon"

Check whether each promise has been fulfilled by a published post. Flag unfulfilled promises.

#### Dimension 6: Cross-Blog References

Check the footer of every post:

- Does every post have a "Previously:" link (except Part 1)?
- Does every post have a "Next:" link (except the latest)?
- Do the link titles match the actual post titles?
- Is the series navigable end-to-end by following footer links?

#### Dimension 7: Content Depth

Compare each post against the series baseline:

- Does it include assembly analysis? (All posts should)
- Does it include Compiler Explorer links? (All posts should)
- Does it include benchmark methodology? (Hardware, compiler, flags)
- Does it include `perf stat` data where claims about CPU behavior are made?
- Is any post significantly thinner than the rest?

#### Dimension 8: Reader Questions

For each post, list 2-3 questions a skeptical reader might ask that the post doesn't answer. Prioritize questions that would become r/cpp comments.

### Step 3: Report

Present findings as a table per dimension with columns: Post, Line/Section, Issue, Priority (must-fix / should-fix / nice-to-have).

### Step 4: Apply Fixes

After presenting the report, ask: "Want me to apply these fixes?"

If yes, apply must-fix and should-fix items. Leave nice-to-have items for the user to decide.

## Style Rules (Series Conventions)

These are the established conventions. The review should flag violations:

- No em dashes. Use " -- " (space-hyphen-hyphen-space) or reword.
- No emojis.
- Voice: conversational, second-person, technical but accessible.
- Assembly: AT&T syntax (GCC default), annotated line-by-line.
- Benchmarks: always specify hardware, compiler version, flags, iteration count.
- Compiler flags: `-O2 -march=skylake-avx512 -fcf-protection -falign-functions=64 -falign-loops=64`
- Compiler Explorer links for every assembly snippet.
- Stdlib source references: pinned to release tags, not `main`.
- Footer: companion repo link, hardware specs, previous/next links.
- Jekyll: use `{% post_url YYYY-MM-DD-slug %}` for internal links, never hardcoded URLs.