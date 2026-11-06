# eventfold

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

## License

MIT. See each package's `LICENSE`.
