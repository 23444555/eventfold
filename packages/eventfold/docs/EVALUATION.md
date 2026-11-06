# Evaluation

**Read the limits first.** This is a small, single-author benchmark on a synthetic corpus. It shows
that the mechanism works on questions of a particular shape; it is not evidence about your data.

> **Update (v4 dataset).** The paraphrase set shipped in v3 was broken: a quarter of the "rewrites"
> asked a different question and were still scored against the original gold (see *Are the rewrites
> even the same question?*). I regenerated it with a stricter contract — same answer or discard,
> 5 candidates per question, 91% pass rate — and re-ran everything. **The v4 numbers in *Clean
> paraphrase set (v4)* are the ones to quote**; the earlier arm tables are kept because they show
> how much a bad paraphrase generator moves the answer.

## Corpus

A fictional homelab operator ("王五") maintaining a home network, a backup array, a rack, a UPS and a
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
DEEPSEEK_API_KEY=... python bench/ab-answer.py --conc 12   # end-to-end answering (optional)
```

Both write JSON (`bench/ab-gen.json`, `bench/ab-result-v3.json`). The 15-question three-arm script
(`bench/ab-coverage.mjs`) is kept for a quicker check; treat it as a subset, not a separate result.

The answering run needs `DEEPSEEK_API_KEY` (or `--key`) and writes `bench/ab-answer-result.json`.
`bench/ab-answer-join.py` cross-tabulates it against the fixed-budget coverage numbers. Nothing in the
build or runtime path calls a model; this is evaluation only.

## End to end: does the model actually answer? (112 instances, 6 arms)

Everything above scores *whether the needed fact made it into the block*. That is a proxy, and it is a
generous one. This section closes the loop: hand each arm's block to a model and see if it answers.

**Setup.** 112 instances × 6 arms = 672 requests to `deepseek-flash`, `temperature=0`, thinking
disabled, 12 concurrent, 221 s, zero errors, 1,227,765 input / 2,491 output tokens. The prompt is
"answer only from the material; if the fact is not there, reply 不知道; ≤20 characters". Arms:
`nil` (no material at all), `1200/vec`, `2400/vec`, `4800/vec`, `4800/sib25`, `4800/ep`.

The judge is mechanical, not a second model: strip punctuation, then accept an answer if it contains
the reference answer, or the evidence sentence, or a digit-bearing token from the evidence
(`88k99`, `a1b2c3d`), or matches an anchor — 13 questions have no content in `evidence` at all (the
field holds a citation like `07:11-30 18:01`) and their real answer lives in `anchors` — or shares
≥0.55 of its character bigrams with an anchor / ≥0.6 with the evidence after removing filler
characters. Abstention is detected first and reported as its own column. The first version of this
judge ignored `anchors` for those pointer-style golds and scored 37.5% where the same answers now score
46.4%: 55 of 672 verdicts changed when that was fixed, which is a reminder of how much of a mechanical
benchmark is the ruler.

| arm | material chars | correct | abstained | wrong | accuracy | originals (44) | paraphrases (68) |
|---|---|---|---|---|---|---|---|
| nil | 0 | 0/112 | **112** | 0 | 0% | 0% | 0% |
| 1200/vec | 1,198 | 49 | 33 | 30 | **43.8%** | 75.0% | 23.5% |
| 2400/vec | 2,398 | 52 | 27 | 33 | 46.4% | 75.0% | 27.9% |
| 4800/vec | 4,797 | 52 | 25 | 35 | **46.4%** | 75.0% | 27.9% |
| 4800/sib25 | 4,797 | 52 | 25 | 35 | 46.4% | 72.7% | 29.4% |
| 4800/ep | 1,970 | 40 | 43 | 29 | **35.7%** | 70.5% | 13.2% |

Two things fall out immediately.

1. **The recall numbers above still overstate usefulness by about a third.** `4800/vec` puts every
   needed fact in the block 79.5% of the time and the model answers correctly 46.4% of the time. If the
   product goal is "the assistant can answer", the honest number for this layer is the second one.
2. **Four times the context buys 2.6 points.** 1,200 → 4,800 characters moves accuracy 43.8% → 46.4%.
   Past ~1.2k characters the limit is not how much you retrieved. The `nil` row also says the model is
   not the problem in the obvious way: with no material it abstained 112/112, so it follows the
   "say 不知道" instruction rather than inventing.

### Where the rest is lost

Joining per-instance coverage (from the fixed-budget run) with answer verdict:

| arm | all legs present | of those correct | legs missing | of those correct |
|---|---|---|---|---|
| 1200/vec | 65 | 39 (60%) | 47 | 10 (21%) |
| 2400/vec | 78 | 46 (59%) | 34 | 6 (18%) |
| 4800/vec | 89 | 48 (54%) | 23 | 4 (17%) |
| 4800/sib25 | 90 | 48 (53%) | 22 | 4 (18%) |
| 4800/ep | 58 | 34 (59%) | 54 | 6 (11%) |

Coverage buys accuracy (60% vs 21% at 1,200 chars) — the pipeline is not decorative. But **even when
every needed fact is in the block, only 53–60% of questions get answered**, and 16 of the 89 covered
instances at 4,800/vec still got 不知道 with the evidence in front of them.

By question type at 4,800/vec: `single 15/27`, `correction 8/21`, `alias 7/18`, `arith 4/9`,
`multi 3/9`, `time 4/7`, `多跳 5/6`, `时间 0/3`.

### The thing I expected to fix it, and why it does not

The obvious story for `correction` ("who changed their mind to what") is that a flat list hides the
superseded value, so rendering a same-slot timeline — `阈值: 0.2 (11-26) → 0.5 (11-30) ← now` — should
pay for itself. Three measurements say it does not:

- **There is almost nothing to render.** In the corpus behind this benchmark (84 events, 838 slot rows),
  exactly **1 of 84 events has a slot with more than one value over time**, and that one is two
  different people's names, not a changed value. The changes people actually ask about (a threshold
  going 0.2 → 0.5) sit in *different episodes* on different days, so a per-episode slot history would
  not span them.
- **When the gold sentence is retrieved, correction questions already work**: 7 of 8 covered correction
  instances at 4,800/vec are answered correctly, and 7 of 8 at 1,200/vec too. The headroom is not in
  the rendering.
- **What fails is retrieval, on the paraphrased half**: 13 of 21 correction instances never get the
  gold sentence into any arm's block.

So the `correction` score is largely a retrieval score in disguise, and a timeline renderer would be a
feature the data cannot pay for. That is worth writing down as a negative result: the episode layer's
formatting story is not "make contradictions legible" — at least not on this corpus.

### Is it a retrieval wall, or a packing bug?

For each instance where the 4,800/vec block is missing a leg, the offline rank of the best fact in that
leg (bge-m3, 1,067 facts, cosine):

| offline rank of the missing leg | legs | note |
|---|---|---|
| 1–5 | 7 | ranked high and still dropped → packing rule |
| 6–20 | 6 | same, weaker |
| 21–50 | 2 | edge of the pool → budget / truncation |
| 51–200 | 13 | near the practical limit of this retriever |
| >200 | 15 | retrieval cannot reach it at any sane pool size |

Median rank of a missing leg is **132**. Roughly a third of the misses are not a packing problem at all
— the retriever never ranks those facts, so no amount of re-formatting or re-ordering helps. (Part of
the "1–5" row is measurement noise: the leg-present test here matches on a 12-character prefix, which
can collide with a sibling fact.)

### Hybrid retrieval, same budget

Since the misses are paraphrase misses, the natural next lever is lexical recall on top of the vectors.
The `hybrid` arm fuses the bge-m3 ranking with a character-bigram BM25 ranking (reciprocal rank fusion,
k = 60) and fills the same character budget:

| cap | vec coverage | hybrid coverage | vec accuracy | hybrid accuracy |
|---|---|---|---|---|
| 1,200 | 58.0% | **60.7%** | 43.8% | — |
| 2,400 | 69.6% | 68.8% | 46.4% | — |
| 4,800 | 79.5% | **81.2%** | 45.5% | **45.5%** |

Coverage moves within noise (paired at 4,800: 6 win / 4 lose, p = 0.75; legs-only 7 vs 7, p = 1.0), and
end to end it is exactly a tie (51/112 both arms, 763,629 input tokens). Adding the lexical arm does not
recover the paraphrases, which makes sense once you look at what the paraphrases do: they delete the
names and domain terms, so there is no lexical hook for either retriever to find.

The remaining lever for that is query expansion — embed a hypothetical answer instead of the question —
not more context, not more structure, and not a better renderer.

## Are the rewrites even the same question?

Every variant in `bench/ab-gen.json` is a rewrite of a base question, and it is scored against the
base question's required facts. One bad rewrite is therefore one free miss. I had a model label all
68 rewrites (base question + base answer + rewrite): **35 same (51%), 16 partial (24%), 17 different
(25%)**.

| Group | Instances | Full recall | Answered (model judge, ok+partial) |
|---|---|---|---|
| base | 44 | 70% | 75% |
| same rewrite | 35 | 80% | 54% |
| partial rewrite | 16 | 75% | 37% |
| different question | 17 | 58% | 23% |

Two things fall out.

1. **Retrieval is not what breaks on rewrites.** Genuine rewrites score *higher* on evidence
   coverage than the base questions (80% vs 70%). The collapse I reported earlier was mostly a
   dataset artifact: a quarter of the "rewrites" ask about something else (one asks who ran the
   rollback, the gold is the 15-minute duration), and a further quarter only partly overlap.
2. **Answering is still a real weakness.** Same question, same facts, 21 points worse (75% to 54%).
   The material is full of names the rewrite deliberately removed, and the model does not connect
   them.

### Judging, again

Mechanical judging was both too strict (some golds are pointers like `06:11-01 09:06`) and too
generous (a substring hit counted as right). Same 560 answers, model judge: strict `ok`
33.9--35.7%, `ok+partial` **50.0--56.2%** by budget. The spread between judges (~9 points) is larger
than the effect of doubling the budget, so the judge belongs in the table, not in a footnote.

### Query expansion (HyDE), offline

Rewrites drop exactly the words both the embedder and BM25 key on, so the obvious fix is to search
with an invented answer rather than the question. 112 generations (8.6k prompt tokens), three
rankings over the same 1,067 facts:

| ranking | median gold rank | top-5 | top-20 | median on rewrites |
|---|---|---|---|---|
| the question | 6 | 46.4% | 68.8% | 14 |
| the hypothetical answer | 5 | 51.8% | 75.9% | 10 |
| RRF(question, hypothetical) | 5 | 53.6% | 75.9% | 9 |

Real but small: +7 points at top-20, and it does not touch the deep tail (a third of the missing
legs rank past 200 either way). One extra model call per query on the hot path is not worth seven
points at this size.

## Clean paraphrase set (v4): what actually survives

Regenerated with `bench/gen-variants.py`: 5 candidates per base question at temperature 1.05, each
one judged by a model against *"can the original answer answer this rewrite, with no extra
information?"*, kept only on `same`, deduplicated, answer leakage dropped. **220 candidates → 200
same (91%) → 128 kept after dedup**, covering all 44 base questions (2–3 each). The old set passed
the same gate only 51% of the time.

172 instances, 244 leg-instances. Same arms, same facts, same budgets.

### Evidence coverage

| cap | vec | hybrid | sib25 | sib50 | structFirst | ep |
|---|---|---|---|---|---|---|
| 1,200 | 69.2% | **71.5%** | 66.9% | 63.4% | 52.9% | 40.7% |
| 2,400 | 76.2% | 77.9% | **77.9%** | 75.6% | 64.0% | 54.7% |
| 4,800 | 80.8% | 84.9% | **86.0%** | 84.3% | 73.3% | 59.3% |

Pairwise against `vec` at the same cap: 4,800 `sib25` **9 wins, 0 losses, p = 0.0039**; `hybrid`
10/3, p = 0.092; `structFirst` 10/23, p = 0.035; `ep` 20/57, p = 3e-05. At 1,200 nothing separates
the vector family (sib25 6/10, p = 0.45), and `ep` is still far behind.

**This reverses one thing I said earlier.** On the broken set, "structure does not pay for its
characters" was true; on genuine paraphrases, sibling facts from the hit episode add ~5 points of
coverage at a generous budget and 9/0 in the paired test. What does *not* survive is the other
half: routing the archive layer on its own (`ep`) is 20–27 points worse than retrieval at every cap.

### Answers

Same five arms handed to the model; a model judge marks `ok` / `partial`, and `ok+partial` is the
number below.

| arm | material | answers (ok+partial) | original questions | paraphrases |
|---|---|---|---|---|
| 1200/vec | 1,198 chars | 61.6% | 75% | 57% |
| 4800/vec | 4,797 | 63.4% | 75% | 59% |
| 4800/hybrid | 4,797 | 62.2% | 77% | 57% |
| 4800/sib25 | 4,798 | 62.2% | 77% | 57% |
| 4800/ep | 2,153 | **50.6%** | 73% | 43% |

Three things to read off this:

1. **The paraphrase collapse was mostly the dataset.** With rewrites that keep the question, the
   gap is 75% → 57–59% instead of 75% → 26%. Real, but a fifth of the size the broken set implied.
2. **Everything except `ep` is the same arm, answerwise.** 61.6 / 63.4 / 62.2 / 62.2 — the same
   families that differ by 5 points on coverage differ by 2 points here, and the budget that
   quadruples the context buys 1.8 points. Retrieval improvements are not reaching the answer.
3. **The answering stage is the wall now.** 63.4% at 4,800 chars, 75% even on original questions
   with every leg present. That is where the next experiment should go, not into packing.

Reproduce: `python bench/gen-variants.py --per 5 --keep 3 --temp 1.05`, then

```
node bench/ab-gen.mjs --variants bench/variants-v4.json --out bench/ab-gen-v4.json
python bench/ab-cap.py --gen bench/ab-gen-v4.json --out bench/ab-cap-v4.json
python bench/ab-answer.py --gen bench/ab-gen-v4.json --blocks bench/ab-cap-blocks-v4.json \
    --out bench/ab-answer-v4.json --arms "1200/vec,4800/vec,4800/hybrid,4800/sib25,4800/ep"
python bench/ab-rejudge-llm.py --in bench/ab-answer-v4.json --out bench/ab-rejudge-v4.json
```

## Fact ids: identity is an affordance, asking for citations is a tax

Every fact line in the material gets its real `[F<id>]` prefix, and the instruction changes only in how
hard it leans on citations. 172 instances, arm `4800/vec`, same judge, same prompt otherwise.

| closing instruction | ok | ok+partial | abstained | cited | citation hit the gold legs |
|---|---|---|---|---|---|
| none (no ids at all) | 51.2% | **63.4%** | 20% | - | - |
| ids, never mention them | 72.1% | **77.3%** | 16% | 0% | - |
| ids, soft ("if it fits, mention it") | 71.5% | 76.2% | 17% | 72% | 56% |
| ids, mandatory, rigid template | 69.2% | 73.8% | 19% | 74% | 48% |
| ids, mandatory, free wording | 66.3% | 70.3% | 23% | 78% | 60% |

Three things to read off this:

1. **Stable names for the lines are worth +14 points on their own** (63.4% -> 77.3%), and they cut
   abstention from 20% to 16%. Copying material verbatim does not explain it (35-38% of answers are
   verbatim in every labelled configuration). A number per line gives the model something to *point
   at*; it stops having to re-read forty near-identical sentences to decide which one is meant.
2. **Any requirement to report provenance is a tax, and the tighter the template the bigger the tax**:
   no mention 77.3% > soft 76.2% (-1.1) > mandated template 73.8% (-3.5) > mandated free wording
   (-7.0). The model spends accuracy on ceremony.
3. **Asked, it does cite; unasked, it never does** (0% -> 72-78%). So this is a choice about what the
   transcript is for, not about model capability.

What that means for the design: label every fact line, permanently, as part of the format. Do not
require a citation line. If an audit trail matters, ask softly and accept ~1 point; never mandate a
template.

Caveat: n = 172 per row, one run each, binomial standard error ~3.6 points. The 63.4 vs 70-77 gap is
solid; the ordering among 77.3 / 76.2 / 73.8 is directional only.

Reproduce: `python bench/ab-answer-ids.py --arms "4800/vec" --close <none|soft|strict|free>`, then
`python bench/ab-rejudge-llm.py --in bench/ab-answer-ids-<close>.json --out bench/ab-rejudge-ids-<close>.json`,
then `python bench/_clean_rejudge.py --answers bench/ab-answer-ids-<close>.json --rejudge bench/ab-rejudge-ids-<close>.json`
for the two closing styles that can leave a stray `（`.

## Letting the model pick first (two-stage): no gain over truncation

The natural companion to fact ids is to let the model *use* them: stage one shows the labelled
material and asks only for the ids worth reading, stage two answers from those lines alone. 172
instances, arm `4800/vec`, same judge.

| material given to the answerer | chars | ok+partial | abstained |
|---|---|---|---|
| everything (single stage, no citation asked) | 4,797 | **77.3%** | 16% |
| truncated to 1,600 chars (control) | 1,572 | 68.6% | 24% |
| its own picks, unioned with the vector block, capped at 1,600 | 1,572 | 69.2% | 23% |
| its own picks only | 291 | 67.4% | 22% |

Stage one picks well enough to be useful on its own: 78% of instances had at least one gold leg among
its picks, and **when the pick contained a gold leg the answerer scored 78.5%** — the same as handing
it everything. The loss is entirely selection recall (22% of instances lose the evidence before the
answerer sees it) plus the smaller material.

But the control kills the idea: **truncating the vector block to the same 1,600 characters scores
68.6%, and letting the model choose what goes in that budget scores 69.2%.** Choosing costs an extra
model call and buys 0.6 points, which is noise at n = 172. Its selection recall (78%) is no better
than plain vector coverage at 4,800 (80.8%), so all the second stage adds is another chance to fail.

So: fact ids are a format improvement, not a routing improvement. The model reading the material is
accurate when the material is there; it does not know better than the retriever which lines matter,
and it does know how to point at a line when asked.

### Attention hint: reordering is free and worthless; one prompt clause is worth 7 points

If selection cannot be trusted as a *filter*, maybe it works as an *attention cue*: show the whole
material, but put the picked lines first. Same 172 instances, same material (4,789 chars, 117 lines),
nothing deleted.

| material | chars | answer prompt | ok+partial | abstained |
|---|---|---|---|---|
| everything, retriever order | 4,797 | single-stage (ids baseline) | **77.3%** | 27 |
| everything, picked lines moved to the front | 4,789 | identical to above | 76.2% | 28 |
| everything, picked lines moved to the front | 4,789 | two-stage ("a short passage of labelled material") | 69.8% | 42 |

Reordering costs 1.1 points, which is noise — **the cue does nothing**. The retriever's own order is
already as good an attention signal as the model's picks.

The third row is the accidentally interesting one. It is the same material and the same picks as the
second row, and the only difference is the system prompt: the two-stage prompt says "你在读**一小段**
带编号的记忆材料" and drops the line "每行形如 [F1234] 内容". That cost **7.5 points and 14 extra
abstentions** on 117 lines of material. A one-clause description of the material is worth more than
every routing intervention tried in this document — the same lesson as the +14 from fact ids, from the
other direction: what the prompt says about the *shape* of the material changes answers; what we do
about the *order* of it does not.

Reproduce:

```
python bench/ab-answer-2stage.py --arms "4800/vec" --topk 12 --cap2 1600
python bench/ab-answer-2stage-union.py --cap2 1600                  # picks ∪ vector block
python bench/ab-answer-2stage-union.py --cap2 1600 --no-picks --out bench/ab-answer-capcontrol.json
python bench/ab-rejudge-llm.py --in bench/ab-answer-2stage.json --out bench/ab-rejudge-2stage.json
```

## Why it says "不知道": the reading contract was too strict

The 27 abstentions in the best arm (16% of 172) looked like a reluctance problem, so I audited them
first. Nineteen of the 27 had **full coverage** — every gold leg was sitting in the block — and the
model still refused. Eight were genuinely missing evidence, where refusing is correct.

So I re-asked the same 27, same material, and varied only what the prompt *permits*:

| contract | what it adds | targets (27 refusals) | control (30 already correct) |
|---|---|---|---|
| `base` | nothing (reproduction) | 2/27 correct, 25 still refuse | 30/30 correct, 0 refuse |
| `derive` | "you may compute, compare, or conclude from it" | 13/27 correct, 7 wrong | 30/30 correct, 0 refuse |
| `bestguess` | "if any sentence is relevant, answer with the closest reading" | **15/27 correct**, 7 wrong | 30/30 correct, 0 refuse |

Both permissions convert two thirds of the refusals into attempts and **cost nothing on the instances
the model already answered** (30/30 in both). Both produce the same 7 wrong answers — `bestguess` just
converts two more of the remaining refusals into correct ones.

Run over the whole set (172 instances, arm `4800/vec`, same judge, no extra calls, no retrieval change):

| contract | ok+partial | abstained | wrong |
|---|---|---|---|
| fact ids, "say 不知道 if not stated" | 77.3% | 27 (16%) | 12 (7%) |
| same + permission to derive / paraphrase | **87.2%** | 6 (3%) | 16 (9%) |

The permission is worth **+9.9 points** — more than every retrieval experiment in this document
combined, and more than the ids themselves. It lands almost entirely on the paraphrase half: variant
`single` 21/32 → 26/32, `跨篇` 0/3 → 2/3, `跨篇·近似` 0/2 → 2/2, `time` 9/15 → 11/15, `alias` 12/17 →
14/17. Base questions were already at ceiling and stay there.

Read carefully, because this is a trade and not a free win: the refusal rate collapses from 16% to 3.5%
and the wrong-answer rate rises from 7.0% to 9.3%. A system that answers "不知道" when the fact is in
front of it fails silently; a system that guesses fails visibly. I would take the permission — but only
with the `不想猜` escape hatch still in the prompt, which is why six refusals survive (the arithmetic
case whose evidence is a timestamp pair, the "how many days" case that needs a subtraction across two
dates, and four whose referent the paraphrase dropped entirely). Those six are honest refusals: the
material really does not answer them directly.

The rule this leaves behind: **tell the model what it may do with the material, not what it must
produce.** Every mandatory format in the previous section cost points; every permission here gained
them, and the one prohibition that remains ("if nothing relevant, say 不知道") is the thing that keeps
it honest.

Reproduce:

```
python bench/ab-abstain-audit.py --preview 25        # the 27 refusals, split by coverage
python bench/ab-abstain-retry.py --control 30        # base / derive / bestguess on those 27 + 30 controls
python bench/ab-rejudge-llm.py --in bench/ab-abstain-retry.json --out bench/ab-rejudge-retry.json
python bench/ab-answer-ids.py --arms "4800/vec" --close bestguess --out bench/ab-answer-bg.json
python bench/ab-contract-table.py                    # 77.3% vs 87.2%
```

## The last 16 errors: the benchmark is holding the old value

After the permission experiment the best arm sits at 87.2% with 16 wrong answers. I dug all 16 out
(`bench/ab-wrong-audit.py`), and the hypothesis I wanted to test — "the model confuses an old value with
the current one" — is half right, in the direction I did not expect: **in most of them the model is
right and the gold is the stale one.**

| class | n | what actually happened |
|---|---|---|
| gold/answer both in the block, question does not say before or after the change | 7 | 阈值 0.8 → 0.5 on 11-20; the gold quotes the 0.8 complaint, the model answers the 0.5 the same day's fact states |
| paraphrase dropped the subject, so the question is ambiguous | 3 | "那个法子里的失效时长" → 黑名单缓存 3600 (11-02) vs 网关 TTL 86400 (11-20); gold picks one, model picks the other |
| gold is a fragment with no answer in it | 4 | `开个复盘会` / `这十二天` as gold for "哪一天 / 多少天" |
| genuine model errors | 5 | 4 of them are the same failure: it takes `21:40 王五喊话追责` as the recovery time and answers "40 分钟" when the block says `21:20 回滚、21:35 恢复`; 1 is an alias mix-up (王五 vs 李四) |

Two readings, and the second is the useful one.

First, the benchmark ceiling: 11 of 16 are the *question set's* problem, not the system's. Corrected for
that, the answer arm is at about **93.6%** (161/172). Every conclusion about "the answer stage is the
wall" has to be read against this number, not against 87.2%.

Second, the only genuinely retrieval/rendering-shaped failure left is **time attribution**: four
instances where two events on the same night carry four timestamps and the model attaches the wrong one.
That is exactly the shape the archival layer is supposed to own — not ranking, not formatting, but
saying which time belongs to which event. It is a very small sample, so treat the four as a lead rather
than a result, but it is the first place in this whole document where the structured layer has a job
that the vector arm provably cannot do.

## Timeline headers do not fix the last four errors -- they are coverage, not attribution

The four genuine-looking errors were all the same: asked how long an incident lasted, the model answers
"40 minutes", which is the 11-24 incident's duration, not the one it was asked about. That reads like a
time-attribution failure, so I built the fix for it: `bench/ab-timeline.py` extracts the date and clock
time from every line in the block and writes a compressed per-day header above the material, changing
no line:

```
【同日时间线（按块内提及的日期与时刻排，未改动材料）】
- 11-05：21:15 王五：命令张伟立刻回滚限流配置 ｜ 21:25 回滚完成、网关重启完毕
- 11-08：21:15 王五：下令立刻回滚 ｜ 21:20 肥鱼：执行 git revert ｜ 21:35 系统重启完毕，连接池恢复正常
- 11-11：09:55 王五：下令肥鱼立刻回滚 ｜ 10:00 肥鱼：执行 git revert ｜ 10:45 一次故障持续约四十分钟
```

34 instances (the 4 targets + 30 previously-correct controls), same arm, same contract (bestguess),
same block, header ≈ 190-290 chars:

| | baseline | with header |
|---|---|---|
| the 4 "40 minutes" errors | 4 wrong | **4 wrong** |
| 30 controls | 28 ok, 2 partial | 28 ok, 2 partial, **1 of them broken** |

Zero fixed, one control broken (`Dq0915#v2`, was right, now wrong). The header does not pay for itself.

The diagnosis matters more than the number: **in those four blocks the evidence is not there.** The base
question — which says "11 月 8 号" — has both `21:20` and `21:35` in its block and answers "15 分钟"
correctly. The four failures are the *paraphrases*, which dropped "11 月 8 号" and left "那次回滚": their
blocks contain `11-08 21:15` but not `21:20`/`21:35` (and the CT1 variants lack the "15 分钟" fact
entirely). Under the bestguess contract the model must answer something, and the nearest candidate is
the 11-24 recap's "整整 40 分钟". No rendering of the block can fix a question that no longer says which
incident it means; it is a coverage failure with a confident wrong answer on top.

So I am reclassifying those four: they are paraphrase defects (a dropped disambiguating constraint), not
model errors. That puts the answer arm at **165/172 = 95.9%** once the question set is fixed, and it
removes the last piece of evidence that a structured layer has a job in the answer path.

The general lesson, now the third time in this document: before building a renderer, check whether the
evidence is in the block at all. Every structural layer proposed here — slot timelines, episode chrome,
same-night headers — was an attempt to fix something the block never contained.

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
- **Two rulers, both imperfect.** The sweep measures evidence recall; the end-to-end section measures
  answers against a mechanical judge whose gold is often the source sentence rather than a reference
  answer. The first is generous (79.5% vs 46.4% on the same arm), the second is harsh on time and
  arithmetic questions and moved 55 verdicts the day I fixed its anchor handling. Trust the ordering,
  and treat neither number as portable.
- And "same budget" is measured in characters of the rendered block, which favours prose over fact
  lists: a flat list of 153 facts spends more tokens than a 5,000-character episode block.
