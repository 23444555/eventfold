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

## A/B: episodes vs every baseline, same budget (112 instances, 12 arms)

The first version of this benchmark had 15 questions and three arms. That was too small to call
anything (5 discordant questions, p ≈ 0.06), and it had a hole in it: the "opponent" was a lexical
baseline, so a reviewer could reasonably say *the gap is just because you never tried vectors*. This
section is the bigger run that closes both holes — and it does not flatter the layer.

Setup (`bench/ab-gen.mjs` + `bench/ab-arms.py`):

- **112 instances** — the 44 questions that have an evidence anchor, plus all 68 documented
  **paraphrases** of them (the variants strip names and domain terms, so routing cannot lean on the
  words in the question). 62 required-evidence legs, counted once per instance → **154 leg-instances**.
- **12 arms**, each fed the *same per-instance character budget* (the episode block's own size,
  median 4,924 chars; the candidate pool for filling was raised to 400 so the budget is actually
  spendable — with a top-50 pool no arm could fill it and "same budget" was a lie).

| # | arm | what it is |
|---|---|---|
| 1 | `nil` | empty context (floor) |
| 2 | `flat8` | 8 facts by character-bigram coverage (a typical memory return) |
| 3 | `flatBudget` | 8 facts, then keep filling until the episode budget is spent |
| 4 | `bm25` | character-bigram BM25 over the same 1,067 facts, filled to budget |
| 5 | `vec` | bge-m3 cosine over the same facts, filled to budget |
| 6 | `vecRr` | bge-m3 top-50 → bge-reranker-base, filled to budget |
| 7 | `vecBudget` | same as `vec`, but filled to the **union arm's** budget (the token control) |
| 8 | `episodeNoBoost` | the episode block with complexity escalation **off** |
| 9 | `episode` | the episode block with escalation **on** (shipped form) |
| 10 | `episodeSeed` | episode block whose router is seeded with the episode ids of the vector top-25 |
| 11 | `episodeSeedGate` | same, but seeds only apply when lexical routing is unsure (best score < 0.5) |
| 12 | `episodePlusVec` | episode block **and** the vector arm, both in context, not trimmed |

Judge: a leg is covered when its exact fact text appears among the block's lines (strict), or when a
line reaches character-bigram Jaccard ≥ 0.55 with it (lenient).

### Overall

| arm | full coverage (strict) | full (lenient) | legs covered | chars | facts | ≈ tokens |
|---|---|---|---|---|---|---|
| nil | 0/112 (0%) | 0/112 (0%) | 0/154 | 0 | 0 | 0 |
| flat8 | 37/112 (33.0%) | 40/112 (35.7%) | 57/154 | 432 | 7.9 | 280 |
| flatBudget | 52/112 (46.4%) | 58/112 (51.8%) | 79/154 | 1,990 | 37.6 | 1,293 |
| bm25 | 62/112 (55.4%) | 63/112 (56.2%) | 90/154 | 2,476 | 56.4 | 1,609 |
| vec | 82/112 (73.2%) | 87/112 (77.7%) | 115/154 | 3,402 | 87.6 | 2,211 |
| vecRr | 77/112 (68.8%) | 83/112 (74.1%) | 109/154 | 1,822 | 46.4 | 1,184 |
| **vecBudget** | **90/112 (80.4%)** | **94/112 (83.9%)** | 126/154 | 6,055 | 153.1 | 3,936 |
| episodeNoBoost | 61/112 (54.5%) | 64/112 (57.1%) | 92/154 | 2,374 | 48.6 | 1,543 |
| **episode** (shipped) | **63/112 (56.2%)** | **66/112 (58.9%)** | 96/154 | 2,417 | 49.9 | 1,571 |
| episodeSeed | 71/112 (63.4%) | 74/112 (66.1%) | 100/154 | 4,029 | 72.2 | 2,618 |
| episodeSeedGate | 66/112 (58.9%) | 68/112 (60.7%) | 99/154 | 3,649 | 65.4 | 2,371 |
| **episodePlusVec** | **94/112 (83.9%)** | **98/112 (87.5%)** | **131/154** | 5,068 | 122.3 | 3,294 |

### The split that matters

| arm | original phrasing (44) | paraphrased (68) | drop |
|---|---|---|---|
| flat8 | 61.4% | 14.7% | +46.7 pt |
| flatBudget | 75.0% | 27.9% | +47.1 pt |
| bm25 | 88.6% | 33.8% | +54.8 pt |
| vec | 88.6% | 63.2% | +25.4 pt |
| vecRr | 86.4% | 57.4% | +29.0 pt |
| vecBudget | 93.2% | 72.1% | +21.1 pt |
| episodeNoBoost | 88.6% | 32.4% | +56.3 pt |
| **episode** | **90.9%** | **33.8%** | **+57.1 pt** |
| episodeSeed | 79.5% | 52.9% | +26.6 pt |
| episodeSeedGate | 81.8% | 44.1% | +37.7 pt |
| episodePlusVec | 100.0% | 73.5% | +26.5 pt |

### What this actually says

1. **Plain vector retrieval beats the episode block by a wide, significant margin.** 73.2% vs 56.2%
   (paired: 30 instances won, 11 lost, p = 0.004; legs 34 vs 13, p = 0.003). The layer is not a better
   retriever than a vector index at the same budget — it is a different one, and on paraphrases it is
   much worse.
2. **The layer's advantage is lexical, and it evaporates when the wording changes.** 90.9% on the
   original phrasing (best single arm; beats same-budget flat recall at 75.0%, p = 0.035) but 33.8% on
   paraphrases — a 57-point drop. Vectors lose 25 points on the same shift. Against BM25 the overall
   result is a **tie** (55.4% vs 56.2%, 13 wins / 12 losses, p = 1.0): the structure buys sharpness in
   the stored vocabulary, not robustness.
3. **The union is the best arm, but most of its gain is "more facts", not structure.** 83.9% vs
   80.4% for `vecBudget`, the vector arm filled to the union's budget: 8 wins / 4 losses,
   **p = 0.39 — not significant**. So "episodes + vectors" ≈ "vectors with an even larger budget".
   The union is at least cheaper per point (5,068 chars for 83.9% vs 6,055 for 80.4%), which is an
   efficiency argument, not a recall one.
4. **Seeding the router with vectors helps the layer a lot and still does not win.** `episodeSeed`
   lifts the block from 56.2% to 63.4% (12 wins / 20 losses against the shipped arm, p = 0.22) by
   fixing the paraphrase collapse (33.8% → 52.9%) at the cost of the original phrasing
   (90.9% → 79.5%), and it spends 1.7× the tokens. Gating seeds on low lexical confidence
   (`episodeSeedGate`) is worse than not gating (58.9%): at a 0.5 gate the router is almost always
   "confident", so seeds rarely apply. Either way it stays below plain `vec`.
5. **The reranker buys nothing here.** `vecRr` 68.8% vs `vec` 73.2% — but note `vecRr` reranked only
   top-50 and therefore could not fill the budget (1,822 chars vs 3,402): this run says "reranking a
   short list does not help", not "reranking never helps".
6. **Complexity escalation is worth almost nothing on this data** (56.2% vs 54.5%, 3 wins / 1 loss,
   p = 0.63). It is cheap (routing p50 **9.1 ms**, p95 14.9 ms), so it can stay — but it is not a
   selling point.
7. **The judge measures evidence recall, not answers.** Everything above says "the facts needed are
   in the block"; none of it says a model then answers correctly.

### What I would change because of this

The measurement does not support "the episode layer retrieves better". It supports a narrower claim:
**the episode layer renders a better block than a flat list of the same size for questions asked in
the stored vocabulary, and it gives free structure (dates, slots, per-episode grouping) that a flat
list has to reconstruct.** Consequences:

- Retrieval should be **vector-first**; the episode layer should be a *rendering* path over whatever
  the retriever found. That is what `episodeSeed` starts to do, and it is why that arm cuts the
  paraphrase collapse from 57 points to 27 (while losing on the original phrasing — the seed policy,
  not the mechanism, is what needs work).
- The honest one-line claim for the README is: **"same budget, better block on questions in your own
  words; no better than a vector index on paraphrases; and it does not significantly beat 'vector +
  more budget'"**. Anything stronger is not supported by these numbers.
- The next experiment worth running is not more arms of this shape, but **union at a fixed token
  cap**: dedupe facts across arms, then render episodes only for the episodes the vector hits land
  in. That targets "cheaper, not bigger", which is where the union currently wins. — *Ran it; see the
  next section. It does not pay off.*

## Fixed budget: does structure pay for its characters? (112 instances, 5 arms × 3 caps)

Same 112 instances, but now every arm is squeezed into the **same character cap** (1,200 / 2,400 /
4,800), and dedupe is shared across arms: identical or mutually-contained lines are kept once, so
sibling facts brought in by the structure are *net new*, not the same sentence reworded.

- `vec` — cosine order, fill to the cap.
- `ep` — the episode block's own order, truncated at the cap.
- `structFirst` — lay out the structure of the events the vector hits (including siblings), then fill
  the rest with vector facts. This is "episodes as the retriever".
- `sib25` / `sib50` — vector fills to 75% / 50% of the cap, then siblings of the hit events come in,
  then vector fills the remainder. This is "structure as a top-up".

| cap | arm | full | legs | chars | facts |
|---|---|---|---|---|---|
| 1,200 | vec | **65/112 (58.0%)** | 97/154 (63.0%) | 1,198 | 32.5 |
| 1,200 | ep | 40 (35.7%) | 61 (39.6%) | 1,070 | 23.5 |
| 1,200 | structFirst | 43 (38.4%) | 62 (40.3%) | 1,198 | 29.7 |
| 1,200 | sib25 | 64 (57.1%) | 94 (61.0%) | 1,197 | 32.9 |
| 1,200 | sib50 | 57 (50.9%) | 84 (54.5%) | 1,198 | 32.2 |
| 2,400 | vec | **78 (69.6%)** | 113 (73.4%) | 2,398 | 63.6 |
| 2,400 | ep | 54 (48.2%) | 86 (55.8%) | 1,849 | 39.3 |
| 2,400 | structFirst | 58 (51.8%) | 83 (53.9%) | 2,398 | 58.1 |
| 2,400 | sib25 | **79 (70.5%)** | 112 (72.7%) | 2,398 | 63.7 |
| 2,400 | sib50 | 72 (64.3%) | 105 (68.2%) | 2,398 | 62.2 |
| 4,800 | vec | 89 (79.5%) | 128 (83.1%) | 4,797 | 123.6 |
| 4,800 | ep | 58 (51.8%) | 91 (59.1%) | 1,970 | 41.4 |
| 4,800 | structFirst | 74 (66.1%) | 102 (66.2%) | 4,798 | 114.3 |
| 4,800 | sib25 | **90 (80.4%)** | 127 (82.5%) | 4,797 | 123.7 |
| 4,800 | sib50 | 87 (77.7%) | 124 (80.5%) | 4,797 | 120.9 |

Paired against `vec` at the same cap (sign test, instances; McNemar, legs):

| cap | arm | won | lost | p(instances) | p(legs) |
|---|---|---|---|---|---|
| 4,800 | sib25 | 6 | 5 | 1.0 | 1.0 |
| 4,800 | sib50 | 6 | 8 | 0.79 | 0.45 |
| 4,800 | structFirst | 8 | 23 | 0.011 | 7e-05 |
| 2,400 | sib25 | 3 | 2 | 1.0 | 1.0 |
| 2,400 | structFirst | 6 | 26 | 0.0005 | 5e-06 |
| 1,200 | sib25 | 3 | 4 | 1.0 | 0.51 |
| 1,200 | sib50 | 3 | 11 | 0.057 | 0.004 |

**What it says:**

1. **Structure never pays for its characters.** At every cap, `vec` wins or ties. The best structural
   arm (`sib25`, a quarter of the budget) is a statistical tie at 1,200 / 2,400 / 4,800 — the point
   estimates move by one instance in each direction. Giving structure half the budget costs real
   points at 1,200–2,400 (50.9% vs 58.0% at 1.2k, p = 0.004 on legs).
2. **"Episodes as the retriever" is significantly worse at every cap**, and the gap *grows* with
   budget: 38.4% vs 58.0% at 1.2k, 66.1% vs 79.5% at 4.8k (p = 0.011 instances / 7e-05 legs). When the
   block gets the whole budget it fills it with whole episodes instead of with facts the question
   needs.
3. **The one thing structure does buy is exact-wording multi-leg questions.** At 4,800 chars `sib25`
   gets 43/44 base instances fully covered (97.7%) vs `vec`'s 40/44 (90.9%) — the siblings complete
   legs the vector only half found. On paraphrases it gives that back (47/68 = 69.1% vs 49/68 =
   72.1%). Net: zero.
4. **The knee is the budget, not the structure.** vec alone goes 58.0% → 69.6% → 79.5% for 1.2k →
   2.4k → 4.8k char caps (+11.6, +9.9). If you want more coverage, buy characters; structuring them
   differently is a tie at best.
5. Note `ep` can't even spend a 4.8k budget: its fact lines total ~1,970 chars. The layer was never
   competing on the same budget — it competes by being short. That is a rendering advantage (cheaper
   blocks), not a retrieval one.

**Recommendation.** Vector retrieval picks; the episode layer *formats* what the retriever found
(dates, slots, per-episode grouping) and may add siblings only when they are free (deduped, inside a
budget you were going to spend anyway). Do not use it to choose candidates.

Reproduce:

```bash
node bench/ab-gen.mjs                    # 112 instances + latency, no GPU needed
python bench/ab-arms.py                  # the 12 arms (needs torch + bge-m3 + bge-reranker-base)
python bench/ab-cap.py --caps 1200,2400,4800 --reserves 0.25,0.5   # fixed-budget arms
```

Both write JSON (`bench/ab-gen.json`, `bench/ab-result-v3.json`). The 15-question three-arm script
(`bench/ab-coverage.mjs`) is kept for a quicker check; treat it as a subset, not a separate result.

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
- **Paired, but one author.** 112 instances is enough to say the episode block loses to plain vector
  retrieval (30/11, p = 0.004) and that it is not significantly better than "vector + more budget"
  (8/4, p = 0.39). It is not enough to pin down single-digit differences, and every question came from
  one person writing them against one corpus. Do not transfer the percentages; transfer the ordering.
- **One corpus, one language.** Chinese, homelab domain, one "user". Nothing here says how it behaves
  on multi-user chat or on conversational small talk.
- **The paraphrase set is the important part.** The 68 rewritten variants are what expose the lexical
  routing (33.8% vs 63.2%). If you only ever test questions asked in the same words as the stored
  text, this layer will look better than it is.
- **The judge measures evidence recall, not answers.** And "same budget" is measured in characters of
  the rendered block, which favours prose over fact lists: a flat list of 153 facts spends more
  tokens than a 5,000-character episode block.
