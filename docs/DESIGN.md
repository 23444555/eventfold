# Design notes

Why the layer exists, what each pass is doing, and the things that were tried and dropped.
Written against the actual implementation, not the plan.

## 1. The shape of the problem

A memory system that stores facts and retrieves top-k answers a lookup. Long conversations are not
lookups. Three failure modes drove every decision here:

**Budget dilution.** Eight retrieval slots, and the same incident is mentioned on five different
days. Five slots are gone before the question is answered. The cause — mentioned once, three weeks
earlier — never arrives.

**Change over time.** "How much is it now?" is answerable only if the *first* value and the *last*
value show up together. Similarity has no concept of "the same slot, later". Two sentences about the
same array on different dates are near-duplicates to a vector index, so it keeps one and drops the
other — exactly the wrong choice.

**No slots.** Facts arrive as prose: "已用 13.4 TB", "现在用到 15.1 TB". Nothing marks these as the
same field, so nothing can diff them.

The episode layer is the smallest thing that fixes all three: group facts into stretches of one
thread of thought, give each group a small comparable slot set, and render a few groups with dates.

## 2. Pipeline

```
facts (JSONL or your DB)
  └─ group    same schema + same subject, ≤ guard_days, not closed, ≤ max_facts
      └─ fold     slot names → closed vocabulary + aliases + bigram similarity
          └─ split    catch-all threads cut at date cliffs
              └─ archive.json   (the only runtime input)
                  └─ route → expand → render   at query time
```

Everything before `archive.json` is a batch job; everything after is pure functions over a loaded
array. That split is deliberate: the expensive, opinionated part runs offline where you can inspect
it, and the runtime stays a 36 KB file with no I/O beyond reading one JSON.

## 3. Grouping

Facts join the current episode when all of these hold:

| rule | value | why |
|---|---|---|
| same `schema` | required | an "incident" and a "deploy" are different objects |
| same `thread` | required | threads are the natural storyline |
| subject match | bigram subset, or Jaccard ≥ 0.5 | "缓存 TTL" vs "缓存 TTL 配置" are the same thing |
| gap | ≤ `--guard-days` (14) | beyond two weeks it is a new episode, not a continuation |
| open | episode not `closed` | an explicitly finished episode must not absorb new facts |
| size | ≤ `--max-facts` (24) | an episode that does not fit in a prompt is not an episode |

When no `subject` is given, the builder derives one: a known slot (`主体`, `对象`, `物品`, `参数名`,
`现象`, `项目`), else the first clause of the text, capped at 24 characters. That fallback is the
weakest part of the builder — it is why an explicit `subject` is worth the trouble upstream.

The real corpus yields 1,067 facts → **58 episodes**, median 15 facts, max 143, 12 threads,
**36 distinct slot names** before folding.

## 4. Slot folding

Free-form extraction produces synonyms per incident type: `现象 / 症状 / 故障 / 报错` are one field.
Without folding, two episodes about the same failure are not comparable, and the renderer prints a
lot of near-duplicate labels.

- A **closed vocabulary per schema** (`examples/mapping.json`) is the target.
- **Aliases** handle the obvious ones.
- **Bigram Jaccard ≥ 0.34** catches the rest (`修复方式` → `修复`).
- Anything unmapped is not dropped and not invented as a new key: it is concatenated into one
  `备注` slot. One bucket, visibly miscellaneous, instead of fifty one-off keys.

Folding is *opt-in*: without `--mapping` the builder passes slot names through unchanged. That is
the right default for a first run — look at what your data actually produces before freezing a
vocabulary.

## 5. Catch-all splitting

A thread named "其他" accumulated 143 facts. Any episode that big is unreadable: the renderer shows
the first few facts and the rest is dead weight. The builder cuts such threads at date cliffs
(`--split-gap` days of silence) so each part is again something a person would describe in one
breath. Parts keep `-p1`, `-p2` suffixes and only the first keeps the merged slot set.

## 6. Routing

At query time the question is routed, not searched:

1. **Subject first.** `routeSubjects()` looks for episodes whose subject, slots or title contain the
   question's subject terms. Structured questions ("what is the array at now") are answered by
   picking the right object, not the nearest sentence.
2. **Similarity fills the rest.** `routeEvents()` scores bigram overlap over title, summary and
   facts, with a day-proximity bonus (`ARCHIVE_DAYBONUS`, 0.06).
3. **Floors, not top-k.** `ARCHIVE_MIN_SCORE` (0.18) and `ARCHIVE_BODY_MIN` (0.34) mean an
   unrelated question gets an empty string instead of eight confident-looking wrong episodes.
   Silence is a feature; a wrong episode in the prompt is worse than nothing.
4. **Subject gate.** A candidate whose subject match is below `ARCHIVE_SUBJ_GATE_MIN` (0.40) is
   rejected even if its text scores high. Without this gate, "the disk in the NAS" pulled in "the
   disk in the laptop" whenever both said 4 TB.
5. **Budget grows with the question.** `complexity()` grades the question; multi-part and
   "how did it end up" phrasing raises the budget (48 facts / 5 episodes / 14 per episode). A
   simple lookup keeps the small budget, so short questions do not pay for long ones.
6. **One hop is evidence.** Sibling episodes (same schema, ≤ `ARCHIVE_SIB_DAYS` 21) and temporal
   neighbours (≤ `ARCHIVE_NEIGH_DAYS` 3) are pulled in. On the multi-hop set, several questions were
   answerable *only* from the neighbouring episode — the fact that closes the story usually sits in
   the next episode, not the matched one.
7. **Render with dates.** Every fact line carries its date. "13.4 TB (2026-02-11)" next to
   "15.1 TB (2026-03-28)" is what lets a model answer a change question without being told to diff.

## 7. Tried and dropped

Recorded because these are the ideas everyone proposes first, and they cost time.

| idea | outcome |
|---|---|
| Bigger budget instead of structure | 40/5/12 → 12/15 on the multi-hop set; 32/5/8 → 9/15. Adding slots to a bad ranking adds noise. |
| Multi-query decomposition (split the question, retrieve per clause) | no gain over subject routing, doubled latency |
| Slots as a joined key for retrieval | too brittle: one missing slot and the fact is unreachable |
| Neighbours *before* the matched episode | confused the model, which then answered from the neighbour's earlier state |
| Neighbours without a day limit | pulled unrelated episodes from months apart; a 3-day and a 21-day window both matter, an unbounded one does not |
| Graph expansion across subjects | one hop is useful, two hops is a different incident |
| Score-threshold-as-router (answer iff top1 > τ) | dead end: unanswerable questions scored 0.96–0.99 while answerable ones top out at 0.63, so no threshold separates them |

## 8. Cost and latency

- Builder: pure Python standard library, no model calls. 1,067 facts build in ~0.1 s.
- Runtime: in-memory scoring over 58 episodes, sub-millisecond; the block is bounded by
  `limit × perEvent`, so prompt cost is predictable (48 facts ≈ 1.5–2 k tokens).
- No index, no database, no service. If `archive.json` is missing, the module returns `''`.

## 9. Known weaknesses

- Subject derivation from prose is heuristic; wrong subjects merge unrelated episodes.
- Fixed 14-day guard splits long-running sagas and merges busy weeks; it is one number for all
  schemas and should probably be per schema.
- The slot vocabulary is hand-maintained and schema-specific — there is no learned mapping.
- Everything was validated on a synthetic corpus (see [EVALUATION.md](EVALUATION.md)).
