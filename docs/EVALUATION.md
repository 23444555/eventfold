# Evaluation

**Read the limits first.** This is a small, single-author benchmark on a synthetic corpus. It shows
that the mechanism works on questions of a particular shape; it is not evidence about your data.

## Corpus

A fictional homelab operator ("老周") maintaining a home network, a backup array, a rack, a UPS and a
few containerised services over roughly four months. 1,067 facts, exported as episodes:

| | |
|---|---|
| facts | 1,067 |
| episodes | 58 |
| conversations / threads | 12 |
| slot names before folding | 36 |
| facts per episode | median 15, max 143 |
| date range | 2026-09-01 → 2026-12-22 |

No real conversation data was used. The corpus was written to exercise the failure modes the layer
targets: values that change over time, incidents that span several episodes, and one catch-all
thread that had to be split.

## Question set

15 multi-part questions, each written so that a single top-k retrieval cannot answer it. Three
shapes:

- **change over time** — "备份阵列最早是多少，换盘之后又是多少？" (needs ≥ 2 facts, different dates,
  same slot)
- **attribution / why** — "那次 502 最后是怎么解决的？" (needs the incident episode *and* its
  follow-up episode)
- **cross-episode recap** — "机柜这一路改过哪些东西？" (needs several episodes along one thread)

## Results

| variant | strict | lenient |
|---|---|---|
| episodes, 32 facts / 5 episodes / 8 per | 9 / 15 | 11 / 15 |
| episodes, 40 facts / 5 episodes / 12 per | 12 / 15 | 13 / 15 |
| **final: dynamic budget + subject gate + sibling/neighbour expansion** | **14 / 15** | **15 / 15** |

The middle rows are the two budgets that were swept before the routing rules were added; the final
row is the shipped configuration.

An earlier version of this document mentioned a "flat facts, no episodes" control (8/15) whose log
could not be found. That number has been withdrawn and re-measured — see the next section, where the
same control now scores 5/15 at 8 facts and 9/15 when given the same character budget.

- **strict** = every fact needed for a correct answer is inside the block.
- **lenient** = the episode containing the answer was retrieved, even if the individual fact was not
  the first line shown.
- Average block length in the final configuration: **~4,100 characters** (~1.5–2 k tokens at the
  configured budget).

The one strict miss is a question whose answer lives in the third episode of a thread; the budget
shows two and the neighbour expansion does not reach three. It is answerable in lenient terms, which
is why the pair is reported: the layer reliably gets you the right part of the timeline, and
occasionally not every fact inside it.

## What the sweep says

- **Structure beats budget.** Going from 32/5/8 to 40/5/12 gained 3 questions; the remaining
  +2 came from routing rules (subject gate, dynamic budget, sibling/neighbour expansion), not from
  more text.
- **Neighbour expansion earns its place.** In the runs that led to 14/15, the fact that closes a
  story sat in the episode *after* the matched one, which is why the final configuration pulls in
  temporal neighbours and same-schema siblings. The intermediate logs for "with vs without" were not
  kept, so this is a direction, not a measured delta.
- **The subject gate is a precision measure**, not a recall one: it did not move the 15-question
  score, it stops the wrong object's episode from outranking the right one when both share
  vocabulary.

## A/B: episodes vs plain fact recall, same budget

`bench/ab-coverage.mjs` runs three arms over the same 15 questions and the same mechanical judge
(each question has a "required evidence set" — a list of legs, each leg a set of interchangeable
facts; a leg counts as covered when any member appears in the block).

| arm | full coverage (strict) | full coverage (lenient) | chars / query | facts / query | ≈ tokens |
|---|---|---|---|---|---|
| **episodes + 8 recalled facts** (shipped) | **14/15 (93%)** | **15/15 (100%)** | 4,684 | 55.3 | 3,045 |
| 8 recalled facts only (typical memory return) | 5/15 (33%) | 6/15 (40%) | 542 | 8.0 | 352 |
| recalled facts, **filled to the same char budget** | 9/15 (60%) | 11/15 (73%) | 3,845 | 59.4 | 2,499 |

The third row is the opponent that matters. It is not a straw man: it spends *more* facts (59.4 vs
55.3) and roughly the same characters, and it still loses 5 questions — because a flat list of 59
facts has no notion of "this episode is about the thing you asked about, and here is what changed
inside it".

Where the episodes win, they win on exactly the shapes the layer was built for:

| question | episodes | same-budget flat |
|---|---|---|
| 十月份批预算的那台测试机，后来买成什么了？ (approved budget → what was actually bought) | 2/2 | 1/2 |
| 从开群到新版本发布，一共过了多少天？ (two ends of a span) | 2/2 | 1/2 |
| 折腾一圈后，缓存 TTL 最后回到了多少？ (a value that changed and changed back) | 2/2 | 2/2 |
| 两次「汇报说做了、实际没做」分别是哪两个人、什么事？ (two separate denials) | 2/2 | 1/2 |
| 11 月 26 号发现内存里实际还在跑的是哪个阈值的… | 1/2 | 1/2 |

Two honest notes:

- **Cost.** The episode block is ~8.6× the tokens of a bare 8-fact return. For a one-fact question
  ("what model is my keyboard") that is pure overhead — you should not pay it. The layer is for
  questions that span time or sources; the router decides per question, so simple questions never
  trigger it (see DESIGN.md, "budget escalation").
- **The judge measures evidence recall, not answer quality.** It says "the facts needed to answer
  are in the block"; it does not say a model then answers correctly. That keeps the benchmark free
  to run and impossible to overfit by prompt-wrangling, but it is not an end-to-end score.

Reproduce (needs the corpus and its required-sets file):

```bash
node bench/ab-coverage.mjs --qa novels/qa-v2.json --req novels/required-sets.json \
     --archive p0-archive/archive.json --src ../src/archive.mjs
```

## Reproduce

```bash
python build/archive_build.py --facts your_facts.jsonl --out data/archive.json
node test/runtime_test.mjs          # structural checks on the bundled example
```

The 15 questions are not shipped: they name facts in the synthetic corpus and would be trivially
overfittable. If you want them for comparison, the protocol is one paragraph above — write 15
questions of those three shapes against your own corpus, then check whether every needed fact is in
the block.

## Limits

- **Synthetic.** No real chat logs have gone through this pipeline yet. The corpus was designed to be
  solvable by the mechanism, which biases it.
- **Small n.** 15 questions, one author, no confidence intervals. A 2-question difference is noise;
  the 14 vs 9 gap is not (it is 5 questions, but the interval is still wide — treat it as "clearly
  ahead on this shape of question", not as a percentage you can transfer).
- **One corpus, one language.** Chinese, homelab domain, one "user". Nothing here says how it behaves
  on multi-user chat or on conversational small talk.
- **The baseline is lexical, not vector.** The flat arm scores facts by character-bigram overlap
  (that is what the runtime does when no embedding service is present). A vector index with a
  cross-encoder reranker would likely close part of the gap on the single-fact legs. Running that
  comparison is the obvious next step; it has not been done.
