# Series design: producer-consumer histories from HotSpot, for C++ developers

Date: 2026-11-19. Status: structure approved by the author ("narrative-first, with adjustments"); spec awaiting review.
Research: `~/tmp/hotspot-pc-research/` (four verified dossiers, `RULES.md`, the editorial rethink in `fable-rethink.md`) and `~/tmp/hist-8238761/NOTES.md`. Section numbers in brackets below refer to `fable-rethink.md`.

## 1. Goal

Four posts that teach a general C++ developer how to choose a producer-consumer design, and when to deviate from the standard one. HotSpot's history is the evidence: what each design replaced, what was argued in review, and what was rejected and why.

The author's request, in short:
- design patterns explaining *why* an implementation was chosen;
- the history, the discussions, and what was rejected;
- help readers decide when to use which implementation;
- when it is acceptable to deviate from standard implementations.

**Standard implementation** means `std::mutex` + `std::condition_variable` + `std::deque`, or a library queue (boost::lockfree, moodycamel::ConcurrentQueue, folly, a Vyukov MPSC).

**Success criteria**
- Each post works on its own for r/cpp. Post 3 also works on its own for HN.
- A reader who knows no JVM finishes each post with concrete guidance they can apply to C++ code.
- Every factual claim is traceable to a primary source (JBS, commit, review mail, JEP, code at a pinned tag). Inference is labelled.
- The series supports the talk "Mind the Queue: When to Use Which Producer-Consumer Design". The talk carries the map; the posts carry the histories.

## 2. Structure: narrative-first

Each post is built around **one centerpiece history told in date order**, with its wrong turns intact, plus at most two or three short supporting cases. Each history earns one of the talk's questions; the question is what the reader leaves holding, not the post's heading. [§0, §2A]

| # | Working title | Centerpiece | Question it earns | JVM knowledge needed |
|---|---|---|---|---|
| 1 | Pick Two: What Your Queue Does When the Consumer Falls Behind | Async unified logging, 2019-2025: four answers to "the buffer is full" | Overload policy | None (one sentence: log I/O inside a pause extends the pause) |
| 2 | Two Commits, One Day: The Queue HotSpot Added and the Queue It Deleted | 29 Sep 2020: 8238761 turned two handshake slots into a queue; 8212107 turned the VM-operation queue into one slot | What the consumer needs; how many items are really in flight | Two sentences (VM operation, handshake) |
| 3 | A Lock-Free Queue With One User | G1's barrier buffers, 2004-2025: locked FIFO, lock-free FIFO, generalized NonblockingQueue, no queue (JEP 522), deletion | Who pays, how often; do you need a queue at all | Write barrier and card table (primer, plus about 150 words inline) |
| 4 | Who Frees the Node? A Tag, a Grace Period and a Hazard Pointer | ZGC mark stacks, 2018-2026: versioned pointers, a flag nobody could size, hazard pointers, a missing fence | Memory reclamation | Primer, plus about 150 words inline |

**Primer.** "HotSpot for C++ Programmers" (`_drafts/hotspot-for-cpp-programmers.md`, already drafted and reviewed) is published before Post 3 as an optional companion. Every post still defines its few JVM terms inline (budget about five terms per post, each through its C++ analogue). [§4]

**Approved adjustments to the rethink**
1. Post 4's closer pairs the worked example (one scenario taken through all four questions) with a consolidated **"which queue?" decision table**. That table is the one artifact readers bookmark and the talk reuses.
2. Post 3's dependency on the barrier: the primer goes out before Post 3, and Posts 3 and 4 also carry an inline recap of about 150 words, so neither requires the primer.

**Publishing order.** 1, 2, primer, 3, 4. Posts 1-2 batch for r/cpp; Post 3 is the HN candidate.

## 3. Series findings (replace the earlier deviation thesis)

The earlier observation, "correctness-driven deviations held up, throughput-driven ones produced the bugs", was cherry-picked and is dropped [§1.3]. The series argues these instead, each with its counterexamples printed:

1. **The measured wins came from changing the traffic, not the container.**
   - Three lock-free handoff conversions report no significant difference: 8212826, 8225255 and 8237143 (the last with a "not statistically significant" 3.5%).
   - The numbers that moved came from producer filtering (7011379), from making hidden work stealable (8152438, +200%), from producer-side admission control (8368071, 2x-8x when it was bypassed by accident), and from deleting the queue (JEP 522).
   - The three null results come with three different stated reasons. "Batch before going lock-free" is INFERENCE and is written that way.
2. **Lock-free handoffs were justified where a lock was not allowed, not where it was slow.** Österlund, 2017, JDK-8182703: GC barrier locks "need to be out of the way". The C++ equivalents are signal handlers, real-time threads, allocator hooks, and logging from inside a lock.
3. **The bugs sat on the rare path, whatever the motive:** the full buffer, the last element, empty-then-sleep, wraparound, the weakly ordered CPU.
4. **Deviations that lasted mostly removed a guarantee; reversals mostly added machinery.** INFERENCE. Counterexamples to print: the ABP deque, its termination protocol, and ZGC's hazard pointers all added machinery and stayed.

**When to deviate**, as the series states it:
- name the requirement the standard breaks (correctness and operability count, not only speed);
- put the cost where it is cheap;
- handle the failure mode, and deliberately test the rare paths (a tiny buffer, ARM or TSan);
- instrument the traffic assumption the deviation rests on ("rarely >1 in flight", "should be very rare").

## 4. Post outlines

Full step-by-step timelines, with a quote and URL for every step, are in [§3]. These are the binding decisions.

### Post 1. Pick Two
- **Hook:** between 2020 and 2025 the JVM's async logger gave four different answers to what happens to a message when there is no room for it.
- **Centerpiece** [§3, Post 1, steps 1-10]:
  1. Grow (Feb 2020): the wrong first guess, kept in.
  2. Drop the oldest (Mar 2020).
  3. Holmes asks for unbounded buffering; Xin Liu replies "pick two of three": non-blocking, bounded memory, log fidelity (2021).
  4. Ehn's lock-free sketch, declined by the patch author.
  5. Drop the newest, argued in CSR JDK-8264323 (May 2021).
  6. Ships in jdk-17+25. Then the dropped-message leak (8269064) and the semaphore overflow (8269865, filed five weeks after integration).
  7. Ping-pong buffers; the mutex stays (2022).
  8. Sjölen's circular buffer plus stall mode; Holmes: "a policy choice the programmer should be allowed to make" (2024).
  9. Stall mode ships in jdk-25+12 on the old buffers, the circular buffer withdrawn by its author; then a stall-mode startup deadlock (8362282).
- **Supporting cases:**
  - JFR drops the oldest ("most recent event data", JEP 328), and its drop path leaked a lock from JDK 11 to JDK 22 (8244289, fixed with RAII).
  - Compile-queue admission control and its accidental bypass in JDK 25 (8368071).
- **Guidance:**
  - Write the overload policy down before choosing a container.
  - The menu: abort, caller-runs, drop oldest, drop newest, block, grow, coalesce, throttle the producer.
  - Choose drop-newest or drop-oldest by what the data's reader needs.
  - Record loss in-band. Reserve room for control messages.
  - Wake the consumer on a predicate, not an item-counting semaphore.
  - Test the full path with a tiny buffer.
- **Deviation:** the logger kept its mutex. A lock-free log queue is for producers that may not lock, not for slow producers. Cite Liu's 86.8% fewer dropped messages for the ring, and that it was still declined on complexity.
- **Closer:** the C++ sketch.

### Post 2. Two Commits, One Day
- **Hook:** on 29 September 2020 one engineer pushed two consecutive commits; the first turned two slots into a queue, the second turned a queue into one slot. Verified: 6bddeb709d1 is the parent of 431338bcb3c; same author; both first in jdk-16+18.
- **Centerpiece, the queue that shrank:**
  1. 2001: starvation fix in the two-priority queue (JDK-4390175).
  2. 2019: fire-and-forget operations removed, over Holmes's defence.
  3. 2020: batching deleted ("almost never kicks in ... Yet it has caused several bugs").
  4. Sep 2020: one slot ("rarely >1 VM-operations in-flight"); Phillimore asks for a counter on the wait.
- **Centerpiece, the slots that grew:**
  1. An async handshake "may never be executed", so two fixed slots became a queue.
  2. CAS push, O(n) pop under a lock the executor needs anyway; "should be very rare".
  3. 2021 (8266557): pop-then-execute becomes peek, execute, remove, because "empty" is read as "safe to proceed".
  4. A fence-only fix was refused: "relying on implementation details of a generic queue".
  5. Holmes's worry about losing operation order.
- **Supporting cases:**
  - The compile queue: FIFO in 2007, ranked since 2011 under the same monitor. Contention reported in 2023 (8320283, open). The Leyden lock-free prototype hung (8348960).
  - JFR's PostBox as a flag rather than a queue, labelled as a reading of the code.
- **Guidance:**
  - Measure the in-flight count first.
  - A slot plus a condition variable is a queue when depth is 0-1 and requesters wait.
  - "There is work" is a flag (`fetch_or` / `exchange(0)`).
  - A consumer that filters or ranks must scan under its own lock.
  - If anyone reads "empty" as a signal, remove after the work is done.
  - Priorities bring starvation.
- **Deviation:** every structure here is the standard queue with something removed (capacity, head pointer, payload, order). Each rests on a traffic assumption that should be instrumented. One-sided lock-freedom only works when the consumer already holds a lock for another reason.
- **Closer:** instruct the reader to put a depth counter on their own queue.

### Post 3. A Lock-Free Queue With One User
- **Hook:** HotSpot's general-purpose lock-free FIFO had one production user in mainline, and when that user stopped needing a queue the class was deleted as "(somewhat complicated) dead code".
- **Centerpiece** [§3, Post 3, steps 1-11]:
  1. Per-thread buffers from the 2004 G1 paper.
  2. Producer filtering (7011379, 2011).
  3. "The G1 barrier queue locks are a mess" (8182703, 2017).
  4. Three no-difference conversions (2019-2020), each quoted with its own stated reason.
  5. The FIFO's birth, and its last-element patch.
  6. Generalized despite "it's pretty ugly for a general utility" (2021).
  7. Renamed: "the old name suggests stronger guarantees than actually provided".
  8. The 1/8000 ABA (8273383).
  9. Six years of barrier proposals that kept the queue (Man Cao: "removing the store-load fence or the enqueue operation does not reduce overhead much").
  10. JEP 522 (about 50 barrier instructions down to 12, per the JEP).
  11. Deletion (8371922), and Phillimore's "it sounded cool".
- **Required mention:** Leyden's premain branch still uses NonblockingQueue, hence "one user in mainline".
- **Honest cost of no queue:** batching hides work until the pause (8205353, open). JDK-8368935 only if re-verified.
- **Guidance:**
  - Private, plain per-item path; synchronize per batch; then measure.
  - Replace the per-batch mutex when the call site may not lock.
  - Use a stack if order is irrelevant.
  - Do not generalize for a second user who doesn't exist yet.
  - Ask whether the consumer could rediscover work from idempotent shared state.
- **Link:** the blog's Treiber ABA post, at the ABA step.
- **Closer:** the deletion and the reply, with no summary after it.

### Post 4. Who Frees the Node?
- **Hook:** for about six and a half years ZGC shipped a flag its own engineers said users could not set, because of how a lock-free stack avoided ABA. 8209375 (jdk-12+7, Aug 2018) to 8347335 (jdk-25+12, Feb 2025); recheck the start date at drafting.
- **Centerpiece:**
  1. Versioned-pointer Treiber stacks in a reserved range (2018).
  2. The flag bites a 200 GB heap; a traversal-order fix is refused because it "roughly doubles the marking time" (Liden, 2021).
  3. Hazard pointers: "users have no idea what number to put in to this JVM option" (2025).
  4. The missing fence: a use-after-free was possible in JDK 25-27 GA (8391060, fixed in jdk-28+14). Wording: "was possible", not "crashed".
- **Flashbacks:**
  - The task-queue tag forces a fixed ring and an unstealable overflow (+200% when fixed, 8152438); ABP kept over Chase-Lev.
  - LockFreeStack leaves ABA to its client; GlobalCounter's review ("Sorry no, I don't understand what this counter is doing at all").
- **Supporting case:** JFR's plain-store `_pos` (JDK 11 to late 2019). Single writer removed the atomics, not the ordering. Call it an ordering defect, not a crash.
- **Guidance:** choose reclamation by who dereferences and who frees:
  - array slots: a version tag;
  - a lock-serialized consumer: nothing;
  - many readers with batchable frees and enumerable readers: a grace period (liburcu, folly::rcu, C++26 `<rcu>`);
  - few known poppers: hazard pointers (folly::hazptr, C++26 `<hazard_pointer>`);
  - in every case, test the reclaim-side ordering on ARM or with TSan.
- **Deviation:** stay with the published algorithm when a hybrid's gain is unmeasured (ABP). Leave it when its failure mode reaches the user (the flag). The second choice was right and still shipped a use-after-free.
- **Closer:** the worked example ("N threads producing trace events for one background writer") through Posts 1-4, then the consolidated decision table.

## 5. Conventions

- **Length and voice:** 2500-3000 words of prose per post; conversational, second person.
- **Style:** no em dashes and no " -- " in prose; at most one section-ending aphorism; about two "not X, it's Y" contrasts per post. Closers vary as listed above, with no recurring closing heading. No invented anecdotes; use `<!-- AUTHOR: ... -->` where a personal detail belongs.
- **Version pins:**
  - current code at `jdk-27-ga` (815ff4dc);
  - historical code at the tag where it lived (NonblockingQueue last in jdk-25-ga);
  - post-pin facts named with their own tag (8391060 fix jdk-28+14; async UL `:off` jdk-28+18).
  - Before JDK 9, use jdk7/jdk8 bNN tags, never the jdk-9+95 artifact.
- **Citations:** JBS, `github.com/openjdk/jdk/commit/<hash>`, mail.openjdk.org archive URLs, JEPs. Nothing internal; no employer framing. Short quotes for commentary only; never paste HotSpot code (GPLv2) beyond short excerpts.
- **Transfer:** each case names its C++ construct, and each post says where the HotSpot technique does not transfer (for example, JEP 522 and GlobalCounter need every thread to be reachable).
- **External views:** one sentence per post on what moodycamel, boost.lockfree or folly would and would not decide for you (overload policy, wake-up protocol, reclamation domain).

## 6. Verification protocol (at drafting time)

- **Re-verify:** every quote and number used gets re-fetched from its URL, and every version against git, before the post is called ready. Fetch at about 6-second intervals (openjdk.org rate-limits); no User-Agent spoofing.
- **Corrections already established:**
  - Phillimore's quote is from hotspot-dev 2025-November/115107.
  - Liden's quote is from gc-dev 2021-April/034522.
  - The JFR full list is also drained by the VMThread in JFR's safepoint operation.
  - 8269865 was filed five weeks after *integration*.
  - The compile queue was FIFO until 2011.
  - "Lock-free proposed three times" is wrong: one sketch was declined and one circular buffer withdrawn.
  - "Policy changed four times in review" is wrong: it was four answers between 2020 and 2025.
- **Unverified; do not use without checking:** the 2009 TaskQueue `#ifdef LP64` typo; JDK-8368935's ~5%.
- **Thin sourcing; assert only what is sourced** [§5]: the compile queue's "why"; FilterQueue's locked-pop rationale (inference); JFR drop-oldest (one JEP sentence); the PostBox (code reading); ZGC's choice of hazard pointers over RCU (not discussed in review).
- **Gate:** `/review-blog` on each post before it is marked ready; Jekyll build with `--drafts`.

## 7. Open decisions (author)

1. **Measurements.** Either one small reproducible C++ experiment per post on the Xeon, or an explicit up-front statement that this is a history series with quoted numbers. Candidate experiments, per [§5]:
   - handoff cost versus batch size, mutex versus lock-free stack;
   - drop count, one mutex versus two buffers, under producer contention;
   - hazard-pointer pop with and without the fence on ARM.
2. **C++ sketches** (30-50 lines per post): drafted by Claude for the author to review, or written by the author under the hands-on rule.
3. **Talk abstract.** "seven MPSC handoffs use seven different designs" is not established. Reword before the C++Online CFP closes (2026-11-20 00:00 UTC).

## 8. Out of scope

String deduplication, SATB's 60% threshold rationale, the GC termination protocol, and most of JFR's buffer design. Either no rationale is on record, or they don't serve a centerpiece. Also out: new non-HotSpot research. The rethink suggests one external corroboration per post, but that is optional follow-up, not a requirement.
