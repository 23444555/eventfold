# Language support

Chinese is the language this project was built for and measured on. English is **beta**: it
routes, it reads dates, and the guards work once you tell them what an owner looks like — but
the builder and the block scaffolding are still Chinese, and no English benchmark number exists
yet. This file is the exact boundary, so nobody has to guess.

## What works today

| Piece | Chinese (default) | English (beta) |
|---|---|---|
| `routeEvents` / `archiveBlock` | character-bigram IDF | `MEMORY_ARCHIVE_LANG=en` → word + adjacent-word-pair IDF |
| dates in the question | `11月5日`, `2026-11-05`, `11-5` | `Nov 5`, `5 November`, `11/5`, `2026-11-05` |
| date stripping before scoring | yes | yes (so a date anchors instead of scoring) |
| subject guard | `people` + Chinese owner words | `people` + `EN.ownerWords` |
| provenance guard | verbatim tokens (numbers, versions) | `EN.token` = only tokens carrying a digit |
| structure guard | `> 一句话：` | `EN.summary` (`> TL;DR:` / `> Summary:`) |
| builder (`build/archive_build.py`) | Chinese dates, sentences, slots | **not ported** |
| rendered block scaffolding | `【事件档案】`, `### date title（N 条事实）` | **not ported** (facts render fine, headers are Chinese) |
| `complexity()` / `routeStory()` / `routeSubjects()` | Chinese morphology | **not ported** |

## Use it

Guards:

```js
import { createGuard, EN } from 'fold-guard';

const guard = createGuard({
  sections: ['Status'],
  summary: EN.summary,
  labels: EN.labels,
  ownerWords: EN.ownerWords,
  token: EN.token,
  people: ['Marcus', 'Dana'],        // "someone else" in your corpus
});
const { ok, doc, untraceable, removedSubject } = guard.run({ text: digest, material });
```

Runtime:

```bash
MEMORY_ARCHIVE_JSON=./archive.en.json MEMORY_ARCHIVE_LANG=en node -e "
  import('./packages/eventfold/src/archive.mjs').then(m => console.log(m.archiveBlock('what happened to the disk on Nov 5')));
"
```

Without `MEMORY_ARCHIVE_LANG` nothing changes: the default is `zh`, byte for byte.

### Why the English provenance guard only checks digit-bearing tokens

The Chinese guard treats a run of ASCII/digits as a claim (`A100`, `4090`, `TTL`, `2800`). That
works because Chinese prose rarely contains words made of Latin letters, so anything Latin is
a model number or a code.

English prose is made of words, and words are exactly what a summary is allowed to paraphrase —
"swapped disk 5" and "replaced disk 5" are the same claim. If the guard checked words, every
paraphrase would look like a fabrication. So `EN.token` checks only the part that must survive
verbatim: numbers, model codes, versions, units (`H100`, `4090`, `26`, `15.1 TB`, `p95`).
Acronyms without digits (`NVMe`, `SSD`) are left alone; add them to `whitelist` if they matter.

This is a real trade-off, not a bug: English provenance is stricter about numbers and looser
about nouns. Documented here so a reviewer can disagree with it deliberately.

## Adding a language

A pack is a plain object — copy `packages/eventfold/src/lang.mjs` (`ZH`, `EN`) and fill:

| Field | Meaning |
|---|---|
| `name` | `'zh'`, `'en'`, … |
| `stopWords` | filler removed from the question |
| `queryOnly(s)` | query with filler removed (per token, not by substring) |
| `grams(s)` | `Set` of what counts as "the same words" |
| `parseDate(q)` | `[month, day]` or `null` |
| `stripDates(s)` | the same dates removed from a string |
| `dayParts(d)` | `[month, day]` from a stored `YYYY-MM-DD` |

Then register it in `LANGS` and `getLang()`. `archive.mjs` calls the active pack for all seven
operations; no other file needs to know.

For the guards, a preset is just parameters — see `EN` in `packages/fold-guard/src/index.mjs`.
A language needs: an owner-word regex, a summary regex, and (optionally) a claim tokenizer.

## Port checklist (what is left)

1. `packages/eventfold/build/archive_build.py` — date parsing (`2026年11月5日`, `今晚`), sentence
   splitting (`。！？`), slot extraction. Needs a Python-side pack; the runtime pack does not
   reach it.
2. `packages/eventfold/src/archive.mjs` render scaffolding — `【事件档案】` header, `### … （N 条事实）`,
   `槽位：`, `这类事的标准过程：`, story-mode labels. ~40 string sites.
3. `complexity()`, `routeStory()`, `routeSubjects()` — the regexes encode Chinese connectives
   (`先后`, `最后`, `前前后后`). An English pack needs its English equivalents, not a translation.
4. `packages/fold-guard/src/index.mjs` defaults — `DEFAULT_OWNER_WORDS` and `DEFAULT_SUMMARY`
   stay Chinese; the presets are opt-in on purpose.

## Tests

- `packages/eventfold/test/lang_test.mjs` — English archive, English question, asserts the disk
  episode routes first and the dated fact renders.
- `packages/fold-guard/test/english.test.mjs` — English structure, subject and provenance,
  including the failure mode you must configure around: with the default Chinese owner words,
  an English line that mentions another person is dropped.

Run both with `npm test` at the repo root. If either fails, the "English beta" claim in the
README is false.
