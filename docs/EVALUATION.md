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

## A/B: episodes vs every baseline, same budget (112 instances, 9 arms)

The first version of this benchmark had 15 questions and three arms. That was too small to call
anything (5 discordant questions, p ≈ 0.06), and it had a hole in it: the "opponent" was a lexical
baseline, so a reviewer could reasonably say *the gap is just because you never tried vectors*. This
section is the bigger run that closes both holes.

Setup (`bench/ab-gen.mjs` + `bench/ab-arms.py`):

- **112 instances** — the 44 questions that have an evidence anchor, plus all 68 documented
  **paraphrases** of them (the variants strip names and domain terms, so routing cannot lean on the
  words in the question). 62 required-evidence legs, counted once per instance → **154 leg-instances**.
- **Nine arms**, all fed the *same character budget per instance* (the episode block's own size,
  median 4,924 chars):

| # | arm | what it is |
|---|---|---|
| 1 | `nil` | empty context (floor) |
| 2 | `flat8` | 8 facts by character-bigram coverage (a typical memory return) |
| 3 | `flatBudget` | 8 facts, then keep filling until the episode budget is spent |
| 4 | `bm25` | character-bigram BM25 over the same 1,067 facts, filled to budget |
| 5 | `vec` | bge-m3 cosine top-k over the same facts, filled to budget |
| 6 | `vecRr` | bge-m3 top-50 → bge-reranker-base, filled to budget |
| 7 | `episodeNoBoost` | the episode block with complexity escalation **off** |
| 8 | `episode` | the episode block with escalation **on** (shipped form) |
| 9 | `episodePlusVec` | episode block **and** the vector arm, both in context, not trimmed |

Judge: a leg is covered when any of its interchangeable facts appears in the block (strict =
normalized-text containment; lenient = character-bigram Jaccard ≥ 0.55).

### Overall

| arm | full coverage (strict) | full (lenient) | legs covered | chars | facts | ≈ tokens |
|---|---|---|---|---|---|---|
| nil | 0/112 (0%) | 0/112 (0%) | 0/154 | 0 | 0 | 0 |
| flat8 | 37/112 (33.0%) | 40/112 (35.7%) | 57/154 (37.0%) | 432 | 7.9 | 280 |
| flatBudget | 52/112 (46.4%) | 58/112 (51.8%) | 79/154 (51.3%) | 1,990 | 37.6 | 1,293 |
| bm25 | 62/112 (55.4%) | 63/112 (56.2%) | 90/154 (58.4%) | 2,476 | 56.4 | 1,609 |
| vec | 76/112 (67.9%) | 82/112 (73.2%) | 109/154 (70.8%) | 1,821 | 46.5 | 1,183 |
| vecRr | 77/112 (68.8%) | 83/112 (74.1%) | 109/154 (70.8%) | 1,822 | 46.4 | 1,184 |
| episodeNoBoost | 61/112 (54.5%) | 64/112 (57.1%) | 92/154 (59.7%) | 2,374 | 48.6 | 1,543 |
| **episode** (shipped) | **63/112 (56.2%)** | **66/112 (58.9%)** | **96/154 (62.3%)** | 2,417 | 49.9 | 1,571 |
| **episodePlusVec** | **91/112 (81.2%)** | **95/112 (84.8%)** | **128/154 (83.1%)** | 3,686 | 85.6 | 2,395 |

### The split that matters

| arm | original phrasing (44) | paraphrased (68) | drop |
|---|---|---|---|
| flat8 | 61.4% | 14.7% | +46.7 pt |
| flatBudget | 75.0% | 27.9% | +47.1 pt |
| bm25 | 88.6% | 33.8% | +54.8 pt |
| vec | 86.4% | 55.9% | +30.5 pt |
| vecRr | 86.4% | 57.4% | +29.0 pt |
| episodeNoBoost | 88.6% | 32.4% | +56.3 pt |
| **episode** | **90.9%** | **33.8%** | **+57.1 pt** |
| episodePlusVec | 100.0% | 69.1% | +30.9 pt |

### What this says, including the parts I do not like

1. **On the original phrasing the episode block is the best single-source arm** — 90.9% vs the
   same-budget flat list at 75.0% (paired: 17 questions won, 6 lost, p = 0.035; legs 23 vs 6,
   p = 0.0023). So the earlier 14/15 vs 9/15 claim was directionally right on this shape of data.
2. **On paraphrases the episode block collapses to 33.8%, and pure vector retrieval holds 55.9%.**
   The drop is 57 points for the episode block versus 30 for vectors. That is not a tuning detail —
   `archive.mjs` routes on characters, subjects and slot aliases, and the variants remove exactly
   those hooks. The layer's advantage is **lexical**, and it disappears the moment the user does not
   reuse the wording that got stored.
3. **Against BM25 the episode block is a tie overall** (55.4% vs 56.2%, 13 wins / 12 losses,
   p = 1.0). The structure buys sharpness on questions asked in the original words, not robustness.
4. **The reranker buys nothing here** (vecRr 68.8% vs vec 67.9%): with a fixed character budget the
   reranker mostly reorders candidates that both arms already fit. Reranking pays when the candidate
   list is long and the budget is tight, which is not this setup.
5. **The union is the only arm that is clearly better than everything else** — 81.2% overall, 100%
   on the original phrasing, 69.1% on paraphrases, 28/0 against the shipped arm (p < 0.0001 at query
   level, 32/0 at leg level). It costs ~1.5× the tokens (2,395 vs 1,571), which is the honest price.
6. **Complexity escalation is worth almost nothing on this data** (56.2% vs 54.5%, 3 wins / 1 loss,
   p = 0.63). It is cheap (the routing call is p50 **9.1 ms**, p95 14.9 ms), so it can stay, but do
   not sell it as a win.
7. **The judge still measures evidence recall, not answers.** Everything above says "the facts needed
   are in the block"; none of it says a model then answers correctly.

### What I would change in the runtime because of this

The routing decision should not be lexical-only. The cheapest fix that this benchmark supports is
**vector seeds for the router**: embed the question, take the top-N facts, and use their
`episode_id`s as additional seeds for `routeEvents` — then keep the structured expansion (which is
what wins on the original phrasing) on top. The union arm here is the upper bound of exactly that
change at 1.5× tokens; a seed-based version should get most of it at close to the current cost. That
work is not done, and until it is, this file should not be quoted as "episodes beat vector search".

Reproduce:

```bash
node bench/ab-gen.mjs                    # 112 instances + latency, no GPU needed
python bench/ab-arms.py                  # the 9 arms (needs torch + bge-m3 + bge-reranker-base)
```

Both write JSON (`bench/ab-gen.json`, `bench/ab-result-v2.json`). The 15-question three-arm script
(`bench/ab-coverage.mjs`) is kept for the smaller, cheaper check — its numbers are the ones in the
older table above; treat them as a subset of this run, not as a separate result.


## Reproduce

```bash
python build/archive_build.py --facts your_facts.jsonl --out data/archive.json
node test/runtime_test.mjs          # structural checks on the bundled example
```

The 44 questions and their 68 paraphrases are not shipped: they name facts in the synthetic corpus
and would be trivially overfittable. The protocol is in the A/B section above — write questions of
those shapes against your own corpus, then check whether every needed fact is in the block.

## Limits

- **Synthetic.** No real chat logs have gone through this pipeline yet. The corpus was designed to be
  solvable by the mechanism, which biases it.
- **Paired, but one author.** 112 instances is enough to say the union arm wins (28/0, p < 0.0001)
  and that the episode block loses to vectors on paraphrases (27/14, p = 0.06 — *just* above the
  line). It is not enough to pin down single-digit differences, and every question came from one
  person writing them against one corpus. Do not transfer the percentages; transfer the ordering.
- **One corpus, one language.** Chinese, homelab domain, one "user". Nothing here says how it behaves
  on multi-user chat or on conversational small talk.
- **The paraphrase set is the important part.** The 68 rewritten variants are what expose the lexical
  routing (33.8% vs 55.9%). If you only ever test questions asked in the same words as the stored
  text, this layer will look better than it is.
- **The judge measures evidence recall, not answers.** And the budget arm gives every baseline the
  episode block's footprint, so "same budget" is generous to the baselines on characters and mean on
  tokens (a flat list of facts packs less per character than structured prose).
