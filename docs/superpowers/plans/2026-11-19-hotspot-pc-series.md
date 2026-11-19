# HotSpot Producer-Consumer Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce four review-ready blog drafts (plus a revised primer) that teach C++ developers how to choose a producer-consumer design, using verified HotSpot history.

**Architecture:** Every post has three artifacts:
- a draft in `_drafts/`;
- a sources sidecar (YAML) listing every quote, version claim and forbidden phrasing;
- a C++ sketch with a stress harness.

Three small tools turn the spec's verification protocol into pass/fail checks, so a post is "ready" only when the sources verify, the lint passes, the sketch is TSan/ASan clean, the site builds and `/review-blog` is clean.

**Tech Stack:** Jekyll (Chirpy), Markdown, Python 3.10 (PyYAML 6.0.3, pytest 6.2.5), GCC 14.3.0 (`~/utils/mamba/envs/gcc14/bin/x86_64-conda-linux-gnu-g++`) with `-fsanitize=thread` / `address,undefined`, git (`~/work/git/zulu` for first-tag checks).

**Spec:** `docs/superpowers/specs/2026-11-19-hotspot-pc-series-design.md`. Research: `~/tmp/hotspot-pc-research/` (dossiers; `fable-rethink.md` §3 holds every outline step with its quote and URL).

## Global Constraints

- **Post length:** 2500-3000 words of prose per post (front matter, fenced code and tables excluded).
- **Dashes:** no em dashes, and no " -- " in prose. " -- " is allowed only in Mermaid, assembly comments and table N/A cells.
- **Tells:** at most one section-ending aphorism per post; at most about two "not X, it's Y" contrasts per post.
- **Closers:** no recurring closing heading (Key Takeaways / What This Means for You / What I Learned / Conclusion). The closer types are fixed: Post 1 ends on the C++ sketch, Post 2 on an instruction to measure, Post 3 on the deletion and the reply, Post 4 on the worked example plus the decision table.
- **No invented anecdotes.** Put `<!-- AUTHOR: ... -->` where a personal detail belongs. AUTHOR comments are removed before publishing.
- **JVM terms:** about five per post, each defined once through its C++ analogue.
- **Version pins:**
  - Current code at `jdk-27-ga` (815ff4dc).
  - Historical code at the tag where it lived (NonblockingQueue: jdk-25-ga).
  - Post-pin facts named with their own tag (8391060 fix: jdk-28+14; async UL `:off`: jdk-28+18).
  - Before JDK 9, use jdk7/jdk8 `bNN` tags, never the jdk-9+95 artifact.
- **Citations:**
  - Allowed sources: JBS, `https://github.com/openjdk/jdk/commit/<hash>`, mail.openjdk.org archive URLs, JEPs.
  - Nothing internal; no employer framing.
  - Short quoted excerpts only. Never paste HotSpot code (GPLv2) beyond short excerpts.
- **Measurements:** none new. Each post states up front, in one or two sentences, that it is a history post: the numbers are quoted from the cited engineers, not measured by the author. The blog's earlier measured posts are linked where they bear on a claim.
- **Context per case:** each case names its C++ construct. Each post says where the technique does not transfer. Each post has one sentence on what moodycamel / boost.lockfree / folly would and would not decide for you.
- **Fetch etiquette:** fetch openjdk.org at about 6-second intervals. No User-Agent spoofing (the auto-mode classifier blocks it).
- **Version control:** no commits and no pushes without the author's explicit OK. Every task ends at a checkpoint.
- **Where each series finding (spec §3) is stated,** with its quoted evidence:
  - Finding 1 (traffic, not container): Post 3, the three null results.
  - Finding 2 (lock-free where a lock wasn't allowed): the deviation sections of Posts 1 and 3.
  - Finding 3 (bugs on the rare path): Post 1 (full path) and Post 4 (the fence).
  - Finding 4 (removing guarantees lasted; INFERENCE, with counterexamples ABP / termination protocol / hazard pointers): Post 4's closer, beside the decision table.
- **Internal links:** drafts may use `{% post_url %}` only to targets already in `_posts/`. Links to unpublished series posts are plain text with "(coming soon)" until publication (Task 7).

## Review Focus

1. **A quote that is real but cited to the wrong URL, or paraphrased inside quotation marks.** The research hit two mis-cited URLs (Phillimore, Liden). Expected: every quoted run of 6+ words in a draft matches a sidecar entry, and every entry is found at its URL. *Pinned by Task 1 `test_unlisted_quote_fails` and `test_quote_not_at_url_fails`, and by Step 2 of each post task.*
2. **A version or date claim off by an era.** Examples: the pre-JDK 9 jdk-9+95 artifact; "after it shipped" versus "after integration". Expected: every version claim in a draft has a sidecar `versions` entry whose first tag is computed from git. *Pinned by Task 1 `test_pre_jdk9_artifact_falls_back`.*
3. **Inference stated as fact.** Examples: "ZGC crashed" (8391060 has no field report); FilterQueue's locked-pop rationale; "batch before lock-free"; JFR drop-oldest rationale; "lock-free proposed three times". Expected: each sidecar lists `forbidden` patterns for its post's known inferences, and the lint fails on them. *Pinned by Task 1 `test_forbidden_near_pattern_fails` and by each post's sidecar.*
4. **A C++ sketch with a data race or UB, in a series about concurrency bugs.** Expected: every sketch builds with `-Wall -Wextra -Werror` and runs clean under TSan and ASan+UBSan with a multi-thread stress harness. *Pinned by Task 1 `test_racy_sketch_fails_tsan`.*
5. **A `post_url` to an unpublished draft,** which breaks the non-draft production build. Expected: the lint rejects any `post_url` whose target is not in `_posts/`. *Pinned by Task 1 `test_post_url_to_draft_fails`.*

---

## File Structure

| Path | Responsibility |
|---|---|
| `tools/hotspot_series/verify_sources.py` | Fetch each sidecar URL (with cache), confirm each quote is present after normalization; compute each commit's first tag. |
| `tools/hotspot_series/style_lint.py` | Mechanical style rules, prose word count, quote coverage, forbidden patterns, `post_url` target check, sketch-block sync. |
| `tools/hotspot_series/check_sketch.sh` | Build and run one sketch: plain `-Werror`, TSan, ASan+UBSan. |
| `tools/hotspot_series/tests/` | pytest suite for the three tools, with offline fixtures. |
| `tools/hotspot_series/sketches/<slug>.cpp` | One sketch per post: the embedded core plus a stress `main`. |
| `docs/hotspot-series/<slug>.sources.yml` | Per-post sidecar. |
| `_drafts/<slug>.md` | Per-post draft. |

`tools/` and `docs/` are both in `_config.yml` `exclude`, so nothing here is published.

**Slugs:**
- Post 1: `pick-two-what-your-queue-does-when-the-consumer-falls-behind`
- Post 2: `two-commits-one-day-the-queue-hotspot-added-and-the-queue-it-deleted`
- Primer: `hotspot-for-cpp-programmers` (existing)
- Post 3: `a-lock-free-queue-with-one-user`
- Post 4: `who-frees-the-node-a-tag-a-grace-period-and-a-hazard-pointer`

**Sidecar schema** (all tasks):

```yaml
post: _drafts/<slug>.md
sketch: tools/hotspot_series/sketches/<slug>.cpp   # optional for the primer
quotes:
  - id: liu-pick-two
    text: "we cannot have all three guarantees at the same time: non-blocking, bounded memory and log fidelity"
    url: https://mail.openjdk.org/pipermail/hotspot-dev/2021-March/050459.html
versions:
  - id: ul-integrated
    commit: 41185d38f21
    expect_first_tag: jdk-17+25
forbidden:
  - pattern: "(?i)\\bcrash"
    near: "8391060"          # optional: only within 300 chars of this text
    why: "no field crash on record; write 'a use-after-free was possible'"
```

---

### Task 1: Verification tooling

**Files:**
- Create: `tools/hotspot_series/verify_sources.py`, `tools/hotspot_series/style_lint.py`, `tools/hotspot_series/check_sketch.sh`
- Test: `tools/hotspot_series/tests/test_verify_sources.py`, `test_style_lint.py`, `test_check_sketch.py`, `tests/fixtures/`

**Interfaces:**
- Produces:
  - `python3 tools/hotspot_series/verify_sources.py <sidecar.yml> [--repo ~/work/git/zulu] [--cache ~/tmp/hotspot-pc-research/fetch-cache] [--delay 6]`. Prints one `FOUND`/`MISSING`/`TAG-OK`/`TAG-MISMATCH` line per entry. Exits 0 only if all pass.
  - `python3 tools/hotspot_series/style_lint.py <draft.md> --sources <sidecar.yml>`. Prints one line per violation and `prose_words=N`. Exits 0 only if there are no violations.
  - `tools/hotspot_series/check_sketch.sh <sketch.cpp>`. Exits 0 only if all three builds compile and their runs exit 0.

- [ ] **Step 1: Write failing tests**

`test_verify_sources.py`:
- `test_found_quote_passes`: a `file://` fixture page containing the quote → exit 0.
- `test_quote_not_at_url_fails`: the quote is absent from the fixture → exit 1, and `MISSING` is printed.
- `test_normalization`: fixture text `**non-blocking**, &quot;x&quot;` with curly quotes and newlines matches the sidecar text `non-blocking, "x"`.
- `test_jbs_browse_url_uses_rest`: a `bugs.openjdk.org/browse/JDK-N` URL is rewritten to the REST endpoint (assert the rewritten URL; no network).
- `test_tag_ok` and `test_pre_jdk9_artifact_falls_back`: a temp git repo with tags `jdk7-b24`, `jdk-9+95` and `jdk-17+25` on chosen commits. The first tag uses the `jdk-N+M` form, unless that yields `jdk-9+95`, in which case it uses the lowest `jdk[78]-bNN`.

`test_style_lint.py`:
- `test_em_dash_fails`
- `test_spaced_double_hyphen_in_prose_fails_but_mermaid_ok`
- `test_banned_phrase_fails`, for each of "The irony", "Counterintuitively", "Interestingly", "It's worth noting", "It is worth noting", "As mentioned earlier", "In essence", "It's important to note"
- `test_banned_closing_heading_fails`, for each of "Key Takeaways", "What This Means for You", "What I Learned", "Conclusion"
- `test_word_count_bounds`: 2499 words fails, 2500 passes, 3001 fails; code fences, tables and front matter are excluded
- `test_unlisted_quote_fails`: a 6+ word quoted run not in the sidecar → violation
- `test_forbidden_near_pattern_fails`
- `test_post_url_to_draft_fails`
- `test_cpp_block_not_in_sketch_fails`: a ```cpp block in the draft must be a contiguous, whitespace-normalized substring of the sidecar's sketch file

`test_check_sketch.py`:
- `test_clean_sketch_passes`
- `test_racy_sketch_fails_tsan`: two threads increment a plain `int`
- `test_warning_fails`: an unused variable fails under `-Werror`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/tmp/shubhankar-gambhir.github.io && python3 -m pytest tools/hotspot_series/tests -q`
Expected: failures and errors (the modules do not exist yet).

- [ ] **Step 3: Implement the three tools**
- `verify_sources.py`: `urllib.request` with no custom User-Agent. Cache by URL hash. Sleep `--delay` seconds between network fetches only. Normalize with `html.unescape`, then tag strip, curly→straight quotes, removal of `*`, and whitespace collapse. For JBS, use the REST JSON description plus comments. For tags, use `git -C <repo> tag --contains <commit>`.
- `style_lint.py`: strip front matter, then mask fenced blocks (keeping ```cpp blocks for the sketch check) and table rows before counting words and checking prose rules. The quoted-run regex is `"([^"]{20,})"` with at least 6 words, matched against normalized sidecar texts.
- `check_sketch.sh`: use the GCC 14.3 path above, `-std=c++17 -pthread`. Build 1 is `-O2 -Wall -Wextra -Werror`. Build 2 is `-O1 -g -fsanitize=thread` with `TSAN_OPTIONS=halt_on_error=1`. Build 3 is `-O1 -g -fsanitize=address,undefined` with `-fno-sanitize-recover=all`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tools/hotspot_series/tests -q`
Expected: all pass.

- [ ] **Step 5: Checkpoint.** Show the author the tool output on one fixture. No commit without OK.

---

### Task 2: Post 1, "Pick Two: What Your Queue Does When the Consumer Falls Behind"

**Files:**
- Create: `docs/hotspot-series/pick-two-what-your-queue-does-when-the-consumer-falls-behind.sources.yml`
- Create: `tools/hotspot_series/sketches/pick-two-what-your-queue-does-when-the-consumer-falls-behind.cpp`
- Create: `_drafts/pick-two-what-your-queue-does-when-the-consumer-falls-behind.md`

**Interfaces:**
- Consumes: the three Task 1 CLIs.
- Produces: a draft that the primer's and Post 2's text can refer to by title.

- [ ] **Step 1: Write the sidecar.**
  - *Quotes and versions:* every quote and version for spec §4 Post 1, taken from `fable-rethink.md` §3 Post 1 steps 1-10 and the two supporting cases. Required ids include:
    - `liu-pick-two`
    - `holmes-unbounded` (050386)
    - `stuefe-in-band` (050373)
    - `liu-grumpy` (050620)
    - `holmes-old-clue` (JDK-8264323)
    - `holmes-policy-choice` (085964)
    - `sjolen-withdrawn` (075170)
    - `jep328-recent` (https://openjdk.org/jeps/328)
    - `jfr-raii` (JDK-8244289)
    - `ct-750k` (JDK-8368071)
  - *Versions:* `ul-integrated` 41185d38f21 → jdk-17+25; `stall` ea2c9238492 → jdk-25+12; `jfr-raii-fix` 61932f49a58 → jdk-22+7.
  - *Forbidden patterns:*
    - `(?i)three times` near "lock-free"
    - `(?i)changed four times in review`
    - `(?i)five weeks after (it )?ship`
    - `(?i)async:off` (post-pin and out of scope)

- [ ] **Step 2: Verify the sources.**
  Run: `python3 tools/hotspot_series/verify_sources.py docs/hotspot-series/pick-two-what-your-queue-does-when-the-consumer-falls-behind.sources.yml`
  Expected: every line is `FOUND`/`TAG-OK`. If an entry stays `MISSING` after one retry, remove it and the outline beat that depends on it.

- [ ] **Step 3: Write the sketch and run it.**
  - The core is 30-50 lines: a two-buffer logger.
    - `enum class OverloadPolicy { DropNewest, DropOldest, Block };`
    - `class TwoBufferLog { bool push(std::string_view msg); void consumer_loop(); };`
    - The drop counter is written in-band at the point of loss.
    - One reserved slot is kept for flush/shutdown.
    - The consumer waits on a predicate (`std::condition_variable::wait(lock, pred)`), never a counting semaphore.
  - The harness: 8 producers × 100k messages, a slow consumer, and an assertion that `delivered + dropped == produced` for each policy.
  - Run: `tools/hotspot_series/check_sketch.sh tools/hotspot_series/sketches/pick-two-what-your-queue-does-when-the-consumer-falls-behind.cpp`
  - Expected: exit 0.

- [ ] **Step 4: Write the draft** to spec §4 Post 1.
  - *Front matter:* title, categories `[C++, Concurrency]`, tags, description, `mermaid: true` only if a diagram is used.
  - *Up front:* the history-not-measurement statement in the first screen.
  - *Body:* the timeline in date order, keeping the wrong first guess (grow) and the two supporting cases.
  - *Guidance:* as listed in spec §4.
  - *Deviation section:* the mutex stayed; lock-free is for producers that may not lock. Cite the 86.8% figure as quoted.
  - *Closer:* the sketch core embedded verbatim.
  - *Footer:* sources pinned and a hardware line ("no benchmarks in this post"). No "Previously" link (series opener). "Next: Two Commits, One Day (coming soon)".

- [ ] **Step 5: Lint.**
  Run: `python3 tools/hotspot_series/style_lint.py _drafts/pick-two-what-your-queue-does-when-the-consumer-falls-behind.md --sources docs/hotspot-series/pick-two-what-your-queue-does-when-the-consumer-falls-behind.sources.yml`
  Expected: no violations; `prose_words` between 2500 and 3000.

- [ ] **Step 6: Build.**
  Run: `export PATH=~/utils/mamba/envs/ruby/bin:$PATH:~/utils/mamba/envs/gcc14/bin && JEKYLL_ENV=production bundle exec jekyll build --drafts -d ~/tmp/blog-build-hotspot-series`
  Expected: `done in`, no errors, and `posts/<slug>/index.html` exists.

- [ ] **Step 7: Review.** Run `/review-blog _drafts/<slug>.md`, apply the must-fix and should-fix items, then re-run Steps 2, 3, 5 and 6.

- [ ] **Step 8: Checkpoint.** Present the draft and the review outcome to the author. No commit without OK.

---

### Task 3: Post 2, "Two Commits, One Day: The Queue HotSpot Added and the Queue It Deleted"

**Files:** the sidecar, sketch and draft for slug `two-commits-one-day-the-queue-hotspot-added-and-the-queue-it-deleted`.

**Interfaces:** consumes the Task 1 CLIs; mentions Post 1 by title as plain text.

- [ ] **Step 1: Write the sidecar.**
  - *Quotes and versions:* from `fable-rethink.md` §3 Post 2 steps 1-9 and the supporting cases. Required ids include:
    - `starvation-2001` (JDK-4390175)
    - `holmes-async-defence` (037173)
    - `ehn-10ms` (037175)
    - `osterlund-blue-moon` (040250)
    - `holmes-rarely-1` (043716)
    - `ehn-twice` (043740)
    - `pr151-never-executed` (PR #151 body)
    - `ehn-no-and-no`
    - `has-operation-true` (048553)
    - `ehn-tail-pointer` (048913)
    - `ehn-generic-queue` (049018)
    - `patricio-fence` (048849)
    - `holmes-order` (052163)
    - `leyden-hang` (JDK-8348960)
    - `ct-rate` (compilationPolicy.hpp at jdk-27-ga)
  - *Versions:* `two-commits-a` 6bddeb709d1 → jdk-16+18; `two-commits-b` 431338bcb3c → jdk-16+18; `peek-fix` (8266557's commit; look it up with `git log --grep '^8266557:'`).
  - *Forbidden patterns:*
    - `(?i)pop is not lock-free because` (the rationale is inference; phrase it as the author's reading, e.g. "the record never says why; my reading is ...")
    - `(?i)design discussion` near "PostBox"

- [ ] **Step 2: Verify the sources** (command as in Task 2 with this sidecar). Expected: all `FOUND`/`TAG-OK`. Also check that the parent of `431338bcb3c` is `6bddeb709d1` with `git -C ~/work/git/zulu log -1 --format=%h 431338bcb3c^`.

- [ ] **Step 3: Write the sketch and run it.**
  - The core: a one-slot rendezvous. `class OneSlot { void submit_and_wait(Request&); void serve_loop(); };`. The request lives on the requester's stack. One mutex and two condition variables, with a "waited behind another request" counter (the instrumented assumption).
  - Optional second block: the `fetch_or`/`exchange(0)` flag mailbox.
  - The harness: 16 requesters × 10k requests; assert each request is served exactly once; print the waited-behind count.
  - Run `check_sketch.sh`. Expected: exit 0.

- [ ] **Step 4: Write the draft** to spec §4 Post 2.
  - *Intro:* two-sentence definitions of VM operation and handshake, plus a link note to the primer ("(coming soon)").
  - *Body:* both centerpiece halves in date order; the compile queue and PostBox as short supporting cases, with PostBox labelled as a reading of the code.
  - *Guidance and deviation:* per spec §4.
  - *Closer:* the instruction to put a depth counter on your own queue.
  - *Footer:* Previously: Post 1 (plain text until published). Next: the primer.

- [ ] **Steps 5-8:** lint, build, `/review-blog`, checkpoint, as in Task 2.

---

### Task 4: Primer revision ("HotSpot for C++ Programmers")

**Files:**
- Modify: `_drafts/hotspot-for-cpp-programmers.md`, specifically the opening paragraphs (they currently promise posts "sorted by shape"), the section "Where the Series Goes Next", and the footer.
- Create: `docs/hotspot-series/hotspot-for-cpp-programmers.sources.yml`.

**Interfaces:** consumes the Task 1 CLIs; it is referenced by Posts 3 and 4.

- [ ] **Step 1: Write the sidecar** for the quotes already in the primer: the `satbMarkQueue.cpp` lifecycle comment at jdk-27-ga, the two style-guide quotes, and Yuasa's DOI link target. Add versions for 8367013/8367014 (JDK 26) and 8347335 (jdk-25+12).

- [ ] **Step 2: Verify the sources.** Expected: all pass.

- [ ] **Step 3: Rewrite the series framing.**
  - Change the opening's description of the series from the four shapes to the four histories, by their titles.
  - Replace "Where the Series Goes Next" with a short paragraph pointing to Posts 3 and 4. Keep the glossary.
  - Footer: Previously: Post 2; Next: Post 3 (plain text until published).
  - No other content changes. The SATB, safepoint and toolkit sections were already reviewed.

- [ ] **Step 4: Lint and build** (Task 2 Steps 5-6). Expected: no violations, 2500-3000 prose words, build OK.

- [ ] **Step 5: Checkpoint.**

---

### Task 5: Post 3, "A Lock-Free Queue With One User"

**Files:** the sidecar, sketch and draft for slug `a-lock-free-queue-with-one-user`.

**Interfaces:** consumes the Task 1 CLIs; links the published Treiber ABA post `{% post_url 2026-08-19-lock-free-is-not-free-aba-tagged-pointers-and-a-bounded-ring %}` at the ABA step.

- [ ] **Step 1: Write the sidecar.**
  - *Quotes and versions:* from `fable-rethink.md` §3 Post 3 steps 1-11. Required ids include:
    - `g1-paper` (DOI)
    - `marking-never-complete` (JDK-7011379)
    - `locks-mess` (JDK-8182703)
    - `dodge-barrier-locks` (020018)
    - `nsd-8212826` (024463)
    - `nsd-8225255` (026272)
    - `nsc-8237143` (028317)
    - `pretty-ugly` (034477)
    - `stronger-guarantees` (052667)
    - `like-1-8000` (JDK-8273383)
    - `x-y-floor` (038371)
    - `mancao-fence` (JDK-8226197)
    - `jep522-12` (https://openjdk.org/jeps/522)
    - `no-sync-per-write` (JDK-8342382)
    - `dead-code` (JDK-8371922)
    - `sounded-cool` (115107)
  - *Versions:*
    - 8212826 → jdk-13+5; 8225255 → jdk-14+3; 8237143 → jdk-15+10
    - 8263551 → jdk-17+17; 8268290 → jdk-18+3; 8273383 → jdk-18+32
    - 8342382 → jdk-26+17; 8371922 → jdk-26+25
  - *Forbidden patterns:*
    - `(?i)one (production )?user in its (whole )?life` (write "in mainline")
    - `(?i)because (of )?batching` near "no significant"
    - `8368935` (unless re-verified first)

- [ ] **Step 2: Verify the sources.** Expected: all pass. Also confirm the Leyden premain use of NonblockingQueue before writing "in mainline" plus the Leyden aside: run `curl` on `https://github.com/openjdk/leyden/commit/fa117d9ac2a31f71da13039b6456cbb9103afec7.patch` and grep for `NonblockingQueue`.

- [ ] **Step 3: Write the sketch and run it.**
  - The core: a per-thread buffer with a plain-store fill, a release-store commit, and a batch handoff to a mutex-protected list. `class ThreadLog { void log(void* p); void flush(); };` and `class BatchList { void push_batch(Batch*); Batch* pop_batch(); };`
  - The harness: 8 producers × 1M entries; the consumer drains; assert the total count and that no entry is lost.
  - Run `check_sketch.sh`. Expected: exit 0.

- [ ] **Step 4: Write the draft** to spec §4 Post 3.
  - *Recap:* about 150 words on the write barrier and refinement.
  - *Body:* the timeline steps 1-11 in order, with the three null results each given its own stated reason; the Leyden aside; the cost of no queue (8205353).
  - *Guidance:* per spec.
  - *Closer:* the deletion plus the reply, with no summary after it.
  - *Footer:* Previously: the primer. Next: Post 4.

- [ ] **Steps 5-8:** lint, build, `/review-blog`, checkpoint.

---

### Task 6: Post 4, "Who Frees the Node? A Tag, a Grace Period and a Hazard Pointer"

**Files:** the sidecar, sketch and draft for slug `who-frees-the-node-a-tag-a-grace-period-and-a-hazard-pointer`.

**Interfaces:** consumes the Task 1 CLIs; its closer refers back to Posts 1-3 by title.

- [ ] **Step 1: Write the sidecar.**
  - *Quotes and versions:* from `fable-rethink.md` §3 Post 4 steps 1-6, the flashbacks and the supporting case. Required ids include:
    - `200-percent` (018147)
    - `stay-close-abp` (029261)
    - `aba-left-to-client` (024463)
    - `holmes-counter` (031498)
    - `holmes-better-sense` (031502)
    - `stack-exhausted` and `doubles-marking` (034522)
    - `unlimited-problematic` (JDK-8260267)
    - `users-no-idea` (101290)
    - `only-gc-workers-pop` (101290)
    - `uaf-8391060` (JDK-8391060)
    - `tso-cant-verify` (000868)
    - `eventually` (000869)
  - *Versions:*
    - 8209375 → jdk-12+7 (and check whether the flag predates it under another name, to fix the hook's "six and a half years")
    - 8347335 → jdk-25+12; 8391060 → jdk-28+14; 8152438 (JDK 9); 8195099 → jdk-11+10
  - *Forbidden patterns:*
    - `(?i)\bcrash` near "8391060"
    - `(?i)LP64` (unverified)
    - `(?i)hazard pointers (were chosen )?over (RCU|GlobalCounter) because`

- [ ] **Step 2: Verify the sources.** Expected: all pass. Also re-confirm with `grep -c 'Order the hazard pointers loads' ~/tmp/openjdk-27/src/hotspot/share/gc/z/zMarkingSMR.cpp`, which should print `0` (the fix is absent at jdk-27-ga).

- [ ] **Step 3: Write the sketch and run it.**
  - The core: a Treiber-stack pop protected by a single hazard pointer per thread, with the store-load fence on the reclaim side commented as the 8391060 lesson. `template<class T> class HazardStack { void push(T*); T* pop(); void retire(T*); };`
  - The harness: 4 threads mixing push/pop/retire for 1M operations.
  - Run `check_sketch.sh`. Expected: exit 0 under TSan and ASan.

- [ ] **Step 4: Write the draft** to spec §4 Post 4.
  - *Recap:* the inline recap.
  - *Body:* the centerpiece and flashbacks in order; the JFR `_pos` supporting case, described as an ordering defect.
  - *Guidance:* by who dereferences and who frees.
  - *Deviation:* ABP versus the flag.
  - *Closer:* the worked example ("N threads producing trace events for one background writer") walked through Posts 1-4, then the consolidated "which queue?" table. Its rows are: overload policy, in-flight depth and order, per-item cost and batching, reclamation. Its columns are: situation, standard choice, deviate when, HotSpot example (post).
  - *Footer:* Previously: Post 3. No "Next" (series end).

- [ ] **Steps 5-8:** lint, build, `/review-blog`, checkpoint.

---

### Task 7: Series integration and publish preparation (author-gated)

**Files:**
- Modify: the four drafts plus the primer, moved to `_posts/YYYY-MM-DD-<slug>.md` only when the author sets each date.
- Modify: `CLAUDE.md`, adding a "HotSpot producer-consumer series" list under Published Posts.
- Create: OG cards via `tools/generate_og_cards.py`.

**Interfaces:** consumes all drafts.

- [ ] **Step 1:** For each post the author approves for publication: move it to `_posts/` with the author's date, then replace the plain-text "(coming soon)" references with `{% post_url %}` to targets that are now in `_posts/`.
- [ ] **Step 2: Lint every published post.** Run `style_lint.py` on each. Expected: no violations (the `post_url` targets now exist). Remove all `<!-- AUTHOR -->` comments: `grep -c 'AUTHOR:' _posts/<file>` → 0.
- [ ] **Step 3:** Run `python3 tools/generate_og_cards.py` and add the `image:` front matter for the new posts.
- [ ] **Step 4: Production build without drafts.** Run `JEKYLL_ENV=production bundle exec jekyll build -d ~/tmp/blog-build-hotspot-series`. Expected: no errors, and every footer link resolves.
- [ ] **Step 5: Checkpoint.** Show the author the publish set. Commit and push only on an explicit OK, with no Co-Authored-By trailer (per memory: this is the author's public repo).
