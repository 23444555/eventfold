# fold-guard

**Three fail-closed checks for a document an LLM wrote about someone's facts.**

If you generate a digest — a topic dossier, an episode summary, a "what happened last week" block —
the failure modes are always the same three:

1. **It invents a number.** "4090" when the material says 4070 Ti Super.
2. **It credits the wrong person.** A bullet about a colleague's disk becomes a fact about the owner.
3. **It loses the shape your injector needs**, so the block is silently empty.

`fold-guard` checks all three. It never rewrites prose; it drops the offending bullet and tells you
what it dropped. When in doubt it fails closed: a fold that says less is fine, a fold that says the
wrong person's GPU is not.

Zero dependencies, one file, Node ≥18.

```js
import { createGuard } from 'fold-guard';

const guard = createGuard({
  sections: ['当前状态', '时间线', '待跟进'],
  people: ['李四', '王工'],          // names that are "someone else"
});

const r = guard.run({ text, material });
// { ok, missing, untraceable: ['4090'], removedTrace: 1, removedSubject: 1, doc }
if (!r.ok) retryWithTheModel(r.missing);
use(r.doc);
```

## What each check does

| check | rule | why |
|---|---|---|
| `checkStructure` | required headings and a `> 一句话：` line are present | the injector keys on them; a missing one means the block is empty and nobody notices |
| `untraceable` | every number / model name / version token appears **verbatim** in the material | the cheapest fabrication detector there is |
| `stripGuardLines` | drop bullets that carry an untraceable token | keeps the prose, loses the lie |
| `foreignSubjectLines` | drop bullets that name someone else and never mention the owner | the "王五说…" problem: a colleague's story becomes the owner's fact |

Dates (`2026-11-05`) and small ordinals (`3 条`) are exempt by default — they are structure, not
claims. Every rule is a parameter: `exempt`, `whitelist`, `bullet`, `ownerWords`.

## Design note: why a token check and not a model check

A second model call to verify the first one doubles cost and adds its own failure mode. A substring
check cannot be talked out of a number. It does have a known blind spot — it cannot catch a claim
that is composed entirely of words present in the material ("王五 says the disk is fine" when王五
said no such thing). That is what the subject check is for, and why the two run together.

## Used by

- [dossier](../dossier) — topic dossiers built from an event stream or a fact table.

MIT.
