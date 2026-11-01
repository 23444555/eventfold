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
row is the shipped configuration. A "flat facts, no episodes" control was run early on but its log
was not kept, so no number is claimed for it.

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
- **Small n.** 15 questions, one author, no confidence intervals. A 2-question difference is noise.
- **One corpus, one language.** Chinese, homelab domain, one "user". Nothing here says how it behaves
  on multi-user chat or on conversational small talk.
- **Not compared against a strong baseline.** "Flat facts only" is a weak control. The real
  comparison — a modern vector store with reranking, same budget — has not been run.
