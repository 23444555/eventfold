# eventfold

> **This project is written by an AI agent.** Every line of code, test, document and
> benchmark number in this repository was produced by a DeepSeek-based coding agent running
> in [DeepSeek Harness](https://github.com/deepseek-ai), under human direction from
> [@23444555](https://github.com/23444555) — who reviews the result and owns the decision,
> and does not write the code. See [`MAINTAINERS.md`](MAINTAINERS.md).

> **Chinese first (中文优先).** Chinese is the supported language: the routing index is built
> from Chinese character bigrams, the subject guard matches Chinese names against Chinese
> owner words, the builder parses Chinese date phrasing, and every benchmark number here was
> measured on Chinese corpora. English is **beta**: `MEMORY_ARCHIVE_LANG=en` gives routing and
> date parsing, and `fold-guard` has an English preset — enough to run, not enough to promise
> parity. The rendered block scaffolding is still Chinese. See
> [Language support](#language-support) and [`docs/LANGUAGE.md`](packages/eventfold/docs/LANGUAGE.md).

A **fold layer** that sits on top of a memory backend (a vector store, a fact table, a
conversation log) and gives an LLM two things it can actually trust:

1. **a bounded, dated block of episodes** it can reason over, and
2. **guards** that refuse to emit anything the source material cannot prove.

It is not a memory backend. It does not embed, does not store, and does not retrieve
vectors. It takes what a backend already has and folds it into something smaller that the
model can be shown — and, when the evidence is not there, it folds to nothing.

```
packages/
├── eventfold/    the fold runtime + the Python builder (facts → episodes → dated block)
├── fold-guard/   three fail-closed checks: structure, provenance, subject
└── dossier/      per-topic long-form dossiers, inject at most one, 600 chars
```

`fold-guard` is the only package with zero dependencies and the only one meant to be
consumed on its own; `dossier` depends on it; `eventfold` uses it when a fold has to be
guarded too.

## Install

```bash
npm i fold-guard           # the guards, zero deps
npm i dossier              # dossiers (pulls fold-guard)
npm i eventfold            # the fold runtime (experimental)
```

From this repo:

```bash
npm install                # links the three workspaces
npm test                   # guard tests, dossier tests, runtime tests
npm run test:build         # the Python builder's tests
```

## What a fold looks like

Input is a flat stream of memory units (each with text, a timestamp, a thread, maybe
actors). Output is a list of events and a rendered block:

```js
import { buildIndex, routeEvents, archiveBlock } from "eventfold";

const index = buildIndex(archiveJson);                  // once, ~ms
const picked = routeEvents(question, index);            // bigram IDF + dates + tokens
const block = archiveBlock(question, index, { maxEvents: 3 });
```

The block is dated, bounded, and every line is traceable to a unit. When nothing scores
above the gate, the block is empty — that is a valid answer, and often the right one.

## The three guards

```js
import { createGuard } from "fold-guard";

const guard = createGuard({ sections: ["一句话", "当前状态"], people: ["李四"] });
const result = guard.run(digest, sourceText);
// { ok, missing, missingAfter, untraceable, removedTrace, removedSubject, doc }
```

- **structure** — every required section is present and non-empty;
- **provenance** — every number and quoted phrase appears verbatim in the source (fail-closed:
  a quote that cannot be located is removed, not trusted);
- **subject** — a line that talks about someone else, without an owner word in the same
  sentence, is dropped.

All three are deterministic and cost nothing to run.

## The negative result worth reading first

Feeding a folded dossier *into the prompt* alongside the original chapters made answers
**worse**, not better: 24/92 vs 33/92 passing, twelve questions flipped to fail and three to
pass (McNemar p ≈ 0.04), with identical answer length. The dossier is a **router**, not
material: it should pick the chapter, and the chapter should be what the model reads.

Routing with the fold itself works: on PerLTQA-zh (8,593 questions whose gold is a
pointer into a three-level tree) keyword routing over 1,695 fine-grained events reaches
**68.0% @1 / 83.7% @3**, against 51.6% / 80.4% for dense retrieval alone — and folding the
events up to 303 relation-level nodes costs 22 points (@1 45.5%), because the pointer is
specific and the fold is not. Detail routes; the fold expands.

Evidence and numbers: [`packages/eventfold/docs/`](packages/eventfold/docs/),
[`packages/eventfold/bench/`](packages/eventfold/bench/).

## Development

```bash
npm test                                  # JS tests across workspaces
npm run test:build -w eventfold           # Python builder tests
npm run build:example -w eventfold        # facts → archive.json → block
```

The Python builder (`packages/eventfold/build/`) does four deterministic passes —
group, fold, split, emit — and never calls an LLM. The runtime is one file,
`packages/eventfold/src/archive.mjs`.

## Language support

**Chinese first, English beta.** Every layer of the fold was written against Chinese text, so
the default path stays Chinese. English is not promised — but it is not a dead end either: the
runtime takes a language pack, and the two guards that used to be Chinese-only now take
parameters.

| Layer | Default (Chinese) | English |
|---|---|---|
| `routeEvents` | character-bigram IDF, dates like `11月5日` | `MEMORY_ARCHIVE_LANG=en` → word + word-pair IDF, `Nov 5` / `11/5` / `2026-11-05` |
| subject guard | 18-name roster + owner words `用户 / 号主 / …` | pass your own `people` + `EN.ownerWords` |
| provenance guard | verbatim substring over model numbers / versions | **works**; English preset checks tokens that carry a digit (`H100`, `4090`, `26`, `15.1 TB`) instead of words, because words get paraphrased |
| builder dates | `2026年11月5日`, `11月5日` | **not done** — the Python builder still parses Chinese dates |
| rendered block | `【事件档案】` scaffolding, `### date title（N 条事实）` | **not done** — facts and dates are fine, block headers are Chinese |
| sentence split / folding | `。！？` and colloquial turn boundaries | **not done** — you fold with your own `thread`/`subject` fields |

So: routing, dates-in-questions and both configurable guards travel with you; the builder, the
block scaffolding and the fold heuristics do not. The full port checklist, the pack interface
and a worked English example live in [`docs/LANGUAGE.md`](packages/eventfold/docs/LANGUAGE.md);
the English guards are executable in
[`packages/fold-guard/test/english.test.mjs`](packages/fold-guard/test/english.test.mjs), and
English routing in [`packages/eventfold/test/lang_test.mjs`](packages/eventfold/test/lang_test.mjs).

Issues, pull requests and commit messages: Chinese preferred, English accepted.

## Governance

The owner is hands-off: direction and final accept/reject only, no day-to-day work, and
no routine correspondence — silence means "decide it yourself". Implementation and triage
are done by an AI agent under a standing instruction to decide, execute and report.
See [`MAINTAINERS.md`](MAINTAINERS.md).

## License

MIT. See each package's `LICENSE`.
