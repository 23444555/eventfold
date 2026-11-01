# eventfold

**Turn a flat stream of facts into a handful of episodes you can hand to a model.**

Vector recall gives you fragments: ten unrelated sentences that each mention the same disk.
eventfold gives you the whole thing — *"the array started at 13.4 TB used, a disk went critical,
we swapped it and rebuilt in 26 hours"* — as one bounded block that fits in a prompt.

It is one dependency-free ES module plus a standard-library Python builder. No model calls, no
network, no database. The runtime reads exactly one file: `archive.json`.

```js
import { archiveBlock } from 'eventfold';

// point the runtime at your generated archive.json once, at startup
process.env.MEMORY_ARCHIVE_JSON = './data/archive.json';

const block = archiveBlock('备份阵列现在还剩多少容量？');
// -> "## 备份阵列（2026-02-11 ~ 2026-03-28，2 段）… 已用：13.4 TB … 已用：15.1 TB …"
```

Ten lines, no index to build, no server to run. Put `block` in front of your chat model and it can
answer "how much changed" questions that top-k retrieval structurally cannot.

- [Why this exists](#why-this-exists)
- [Quickstart](#quickstart)
- [What the builder does to your facts](#what-the-builder-does-to-your-facts)
- [Runtime API](#runtime-api)
- [Configuration](#configuration)
- [Status: what is and is not proven](#status-what-is-and-is-not-proven)
- [Design notes](docs/DESIGN.md) · [Evaluation](docs/EVALUATION.md)

## Why this exists

Long-term memory systems usually store facts and retrieve them by similarity. That works for
"what is my graphics card" and fails for anything that is a *process*:

- **Top-k is a budget, not a summary.** Five of your eight slots get eaten by the same incident
  mentioned five times; the explanation that spans three weeks never makes it in.
- **The question is often about change.** "What did it end up at?" needs the first value and the
  last value. Similarity retrieval has no notion of "the same slot, later".
- **Free-form memory has no slots.** Facts arrive as prose, so "已用 13.4 TB" and "现在用到 15.1 TB"
  never meet, and nothing can compare them.

eventfold is the missing layer: an **offline batch job** that groups facts into episodes, folds
free-form slot names onto a small vocabulary, and a **runtime** that routes a question to a few
episodes and renders them as a bounded, dated block.

It is deliberately not a retrieval replacement. Run it *next to* your vector store: vectors for
"which fact", episodes for "what happened".

## Quickstart

Requires Node 18+ and Python 3.9+ (builder only). Nothing to install.

```bash
npm test                                                    # 58 checks, both suites
python build/archive_build.py \
  --facts examples/facts.jsonl \
  --out   examples/data/archive.json                        # 33 facts -> 19 episodes
node -e "process.env.MEMORY_ARCHIVE_JSON='examples/data/archive.json';import('./src/archive.mjs').then(m=>console.log(m.archiveBlock('备份阵列还剩多少容量？')))"
```

Your own facts, one JSON object per line:

```json
{"id": 1, "ts": "2026-03-02 08:15", "thread": "运维", "schema": "incident-online",
 "subject": "反向代理", "text": "反向代理开始间歇性返回 502。",
 "slots": [{"n": "现象", "v": "502"}]}
```

`text` and `ts` are required. `thread`, `schema`, `subject`, `slots`, `closed` are optional and
fall back to heuristics. Then:

```bash
python build/archive_build.py --facts facts.jsonl --out data/archive.json \
  --mapping examples/mapping.json          # optional: fold slot names onto a vocabulary
node test/runtime_test.mjs                 # sanity-check the result
```

## What the builder does to your facts

Four deterministic passes, no LLM:

1. **Group** — facts with the same `schema` and `subject`, no more than `--guard-days` apart, still
   open, and under `--max-facts` become one episode, in time order.
2. **Fold** — a thousand facts produce two hundred slot names (`现象`, `症状`, `报错`). A closed
   vocabulary plus aliases plus bigram similarity (≥ 0.34) maps them onto a handful; anything that
   still does not fit goes into one `备注` bucket instead of inventing a new key.
3. **Split** — a catch-all thread ("其他", "misc") that grew to 300 facts can only ever show its
   first few. It is cut apart at date cliffs so each piece is small enough to fit.
4. **Emit** — one `archive.json`: episodes with `title`, `status`, `first`/`last`, `summary`,
   `slots[]`, `facts[]`.

The runtime then ranks episodes by subject and similarity, expands to the neighbouring episode and
the sibling episode in the same schema, and renders at most N events × M facts into a block with
dates inline, so "old value → new value" is visible in one glance.

## Runtime API

| export | what it does |
|---|---|
| `archiveBlock(query, opts)` | the block you put in a prompt; `''` when nothing matches |
| `flatFacts(query, k)` | just the matching fact lines, no episode framing |
| `routeEvents(query, n)` | scored episodes, if you want to render your own way |
| `routeStory(query, n)` | episodes along one thread, in order |
| `routeSubjects(query, a, b)` | episodes picked by subject, not similarity |
| `complexity(query)` | 0/1/2 grade used to grow the budget for multi-part questions |
| `stats()` | `{events, facts, threads, ...}` from the loaded archive |
| `lastUsedEvents()` | which episodes the last `archiveBlock` actually used |

Options for `archiveBlock`: `{limit, events, perEvent, threads}` — defaults `40 / 5 / 10 / 0`.

## Configuration

All optional, read from the environment:

| env | default | meaning |
|---|---|---|
| `MEMORY_ARCHIVE_JSON` | `./archive.json`, `../data/archive.json` | the one data file |
| `ARCHIVE_MIN_SCORE` / `ARCHIVE_BODY_MIN` | `0.18` / `0.34` | routing floor; below it, silence |
| `ARCHIVE_SUBJ_GATE_MIN` | `0.40` | how sure the subject match must be |
| `ARCHIVE_BOOST` / `_LIMIT` / `_EVENTS` / `_PER` | `on` / `48` / `5` / `14` | budget for multi-part questions |
| `ARCHIVE_NEIGH_DAYS` / `ARCHIVE_SIB_DAYS` | `3` / `21` | how far neighbouring episodes may be |
| `ARCHIVE_FAR_DAYS` | `30` | beyond this, an episode is not "the same stretch of time" |
| `ARCHIVE_DEBUG` | off | log routing decisions to stderr |

Nothing here requires a rebuild.

## Status: what is and is not proven

Read this before you trust the numbers.

- **It has not run on real conversation data yet.** The evaluation in
  [docs/EVALUATION.md](docs/EVALUATION.md) uses a **synthetic** corpus (a fictional homelab
  operator: 1,067 facts → 58 episodes). It is a reproducible benchmark, not field evidence.
- On that corpus, **172 question instances** (44 original + 128 paraphrases, regenerated under a
  strict *same answer or discard* contract that 91% of candidates passed) were run through the arm
  sweep at the same per-question character budget. Full results:
  [docs/EVALUATION.md](docs/EVALUATION.md).
- At a 4,800-character cap, evidence coverage is: sibling facts from the hit episode (`sib25`)
  **86.0%**, hybrid vector+BM25 **84.9%**, plain vector **80.8%**, episode-block-as-retriever 73.3%,
  episode block alone **59.3%**. Paired, `sib25` beats plain vector **9–0 (p = 0.0039)**.
- So structure is worth something, but not the thing it was sold as: **the siblings an episode
  already knows about add ~5 points of coverage at a generous budget, while routing on the episode
  layer alone is 20+ points worse than vector search at every cap.**
- **Asked to answer, everything except the episode-only arm is the same arm.** Model-judged
  `ok+partial`: `1200/vec` **61.6%**, `4800/vec` **63.4%**, `hybrid` 62.2%, `sib25` 62.2%, `ep`
  **50.6%**. Four times the context buys 1.8 points; a 5-point coverage gap buys nothing. Even on
  the original phrasing with every needed fact present only ~75% come out right — **the answering
  stage is the binding constraint, not the packing.**
- So the recommended split is: **vector (plus BM25) retrieval picks the facts; the episode layer
  contributes the siblings it already knows about and formats them** (dates, slots, grouping). Not:
  route on the episode layer alone.
- Earlier numbers in this README came from a paraphrase set that turned out to be broken — a quarter
  of the "rewrites" asked a different question while still being scored against the original gold.
  On that set the layer looked like it collapsed to 33.8% on paraphrases and structure looked
  worthless; both were artifacts. The v4 numbers above replace them, and
  [docs/EVALUATION.md](docs/EVALUATION.md) keeps the old tables as a warning about paraphrase
  generators.
- **Numbering the fact lines is worth 14 points; asking the model to cite them costs points.** Giving
  every line a stable `[F<id>]` lifts correct answers from **63.4% to 77.3%** and cuts abstention from
  20% to 16% (172 instances, same judge, same material). Requiring provenance then taxes it: a soft ask
  gives 76.2%, a mandated template 73.8%, mandated free wording 70.3%. Labels stay in the format;
  citation is a preference, not a rule.
- **Telling the model what it may do with the material is worth another 10 points.** The 27 refusals in
  the best arm (16%) were not missing evidence: 19 had every gold fact in the block. Permitting it to
  compute, compare, or read past the wording — "only say 不知道 when no sentence is relevant" — lifts
  answers from **77.3% to 87.2%**, cuts refusals from 27 to 6, and costs nothing on the 30 instances
  that were already correct. Wrong answers rise from 7.0% to 9.3%: a visible guess instead of a silent
  refusal. Permissions gained what every mandated format cost.
- **A same-night timeline header fixes nothing.** Laying the block's own dates and clock times out in
  order above the material — no line changed, +190–290 chars — leaves the four "40 minutes" errors wrong
  and breaks one control. Their evidence is not in the block: the base question says "11/8" and answers
  correctly, the paraphrases dropped the date. Coverage, not attribution.
- **Letting the model choose which lines to keep is not worth a second call.** Handing it only its own
  picks (291 chars) scores 67.4%; its picks unioned with the vector block at a 1,600-char cap scores
  69.2%; plain truncation to the same 1,600 chars scores 68.6%. When a pick happened to contain a gold
  leg it scored 78.5% — i.e. the model reads well but does not route better than the retriever. Using
  its picks as an attention cue instead of a filter (same material, picked lines moved to the front)
  gives 76.2% against 77.3% — a wash.
- **What the prompt says about the material's shape is worth more than any routing trick.** The same
  material and the same picks, with a prompt that calls 117 lines "a short passage" and drops the line
  describing the `[F1234]` prefix, scores 69.8% instead of 76.2% and abstains 42 times instead of 28.
- Coverage is mechanical (does the block contain every fact the answer needs) and answers are
  model-judged; both are reproducible with `python bench/gen-variants.py`, `node bench/ab-gen.mjs`
  `--variants bench/variants-v4.json`, `python bench/ab-cap.py`, `python bench/ab-answer.py`,
  `python bench/ab-rejudge-llm.py`.
- The honest framing: this is a **reference implementation** of the episode layer. The mechanics
  are sound and tested; the win rate on your data is unknown until you measure it.
- The builder currently reads a JSONL fact stream. It does not talk to any specific memory backend —
  that is intentional, and it is the main thing you would have to write yourself.

## Support

None, and that is on purpose. This is a reference implementation that exists because I needed it;
it is not a product. Issues are welcome as a place to leave notes, but **there is no promise that
anyone reads or answers them**. If your use case needs an answer with a deadline, vendor the code
and own it.

MIT licensed. Counter-examples are the most interesting thing you can send: questions where a plain
top-k beats episodes tell me more than a bug report.
