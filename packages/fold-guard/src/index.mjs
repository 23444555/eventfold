// fold-guard: the three checks that keep a generated "fold" (a dossier, an
// episode summary, any LLM-written digest of someone's facts) from drifting
// into fiction about other people.
//
//   1. structure   — the document has the sections the injector expects
//   2. provenance  — every number / model name / version exists verbatim in the material
//   3. subject     — a bullet about someone else (no owner word in the sentence) is dropped
//
// All three fail *closed*: when in doubt, drop the line. A fold that says less
// is fine; a fold that credits the wrong person is not.
//
// Zero dependencies. Every rule is a parameter, because every corpus has its own
// idea of "someone else".

export const DEFAULT_BULLET = /^\s*[-*]\s/;

export const DEFAULT_EXEMPT = [
  /^\d{1,2}$/, // small ordinals: "3 条", "第 2 次"
  /^\d{4}-\d{2}-\d{2}$/, // dates are structure, not claims
];

export const DEFAULT_OWNER_WORDS = /(用户|号主|主人|本人|你|我)/;

export const DEFAULT_SUMMARY = /^\s*>\s*一句话[:：]\s*\S+/m;

export const DEFAULT_LABELS = {
  title: '# 标题',
  summary: '> 一句话：',
};

// Note the `[ \t]?` and not `\s?`: with `\s?` a token at end of line swallowed the
// newline and the next bullet's dash ("4090\n-"), so nothing ever matched a line.
const TRACE_TOKEN = /[A-Za-z0-9][A-Za-z0-9.+\-]{1,}(?:[ \t]?[A-Za-z0-9.+\-]+)?|\d[\d,.]*/g;

// English prose is made of words, and words get paraphrased — "swapped disk 5"
// vs "replaced disk 5" is the same claim, not a fabrication. Provenance is about
// the part that must survive verbatim: numbers, model codes, versions, units.
// So the English pack checks only tokens that carry a digit ("H100", "4090",
// "26", "15.1 TB", "p95"). Acronyms without digits are covered by the whitelist.
const TRACE_TOKEN_EN = /[A-Za-z]*\d[A-Za-z0-9.+\-]*|\d[\d.,]*/g;

/** English digests: same guards, English summary shape, English owner words,
 *  and a numeric-only provenance tokenizer (see TRACE_TOKEN_EN). */
export const EN = {
  ownerWords: /\b(user|owner|i|me|my|mine)\b/i,
  summary: /^\s*>\s*(tl;?dr|summary|in one line)[:：]\s*\S+/im,
  labels: { title: '# title', summary: '> TL;DR:' },
  token: TRACE_TOKEN_EN,
};

/**
 * @param {object} [opts]
 * @param {string[]} [opts.sections]      headings that must be present, e.g. ['当前状态','时间线']
 * @param {string[]} [opts.people]        names that count as "someone else"
 * @param {RegExp}   [opts.ownerWords]    words that mark a line as being about the owner
 * @param {RegExp}   [opts.summary]       the one-line summary shape (default: `> 一句话：…`)
 * @param {RegExp}   [opts.token]         what counts as a claim that must appear verbatim
 * @param {object}   [opts.labels]        messages used when a required piece is missing
 * @param {RegExp}   [opts.bullet]        what a list item looks like
 * @param {RegExp[]} [opts.exempt]        tokens that are never treated as claims
 * @param {string[]} [opts.whitelist]     extra tokens to let through (TTL, md, …)
 */
export function createGuard(opts = {}) {
  const sections = opts.sections || [];
  const people = opts.people || [];
  const ownerWords = opts.ownerWords || DEFAULT_OWNER_WORDS;
  const summary = opts.summary || DEFAULT_SUMMARY;
  const labels = { ...DEFAULT_LABELS, ...(opts.labels || {}) };
  const bullet = opts.bullet || DEFAULT_BULLET;
  const exempt = opts.exempt || DEFAULT_EXEMPT;
  const whitelist = new Set(opts.whitelist || []);
  const traceToken = (opts.token || TRACE_TOKEN).global
    ? opts.token || TRACE_TOKEN
    : new RegExp((opts.token || TRACE_TOKEN).source, (opts.token || TRACE_TOKEN).flags + 'g');

  function checkStructure(text) {
    const body = String(text || '');
    const missing = [];
    if (!/^\s*#\s+\S+/m.test(body)) missing.push(labels.title);
    if (!summary.test(body)) missing.push(labels.summary);
    for (const s of sections) {
      if (!body.includes(`## ${s}`)) missing.push(`## ${s}`);
    }
    return { ok: missing.length === 0, missing };
  }

  function untraceable(text, material) {
    const hay = String(material || '');
    const seen = new Set();
    const bad = [];
    for (const m of String(text || '').matchAll(traceToken)) {
      const tok = m[0].trim().replace(/[.+\-]+$/, '');
      if (tok.length < 2) continue;
      if (whitelist.has(tok)) continue;
      if (exempt.some((re) => re.test(tok))) continue;
      if (seen.has(tok)) continue;
      seen.add(tok);
      if (!hay.includes(tok)) bad.push(tok);
    }
    return bad;
  }

  function stripGuardLines(doc, bad) {
    const set = new Set((bad || []).map((b) => String(b)));
    if (!set.size) return { doc: String(doc), removed: 0 };
    const lines = String(doc).split('\n');
    const kept = lines.filter((line) => {
      if (!bullet.test(line)) return true;
      for (const b of set) if (line.includes(b)) return false;
      return true;
    });
    return { doc: kept.join('\n'), removed: lines.length - kept.length };
  }

  function foreignSubjectLines(doc) {
    const bad = [];
    for (const line of String(doc).split('\n')) {
      if (!bullet.test(line)) continue;
      const names = people.filter((n) => line.includes(n));
      if (!names.length) continue;
      const rest = names.reduce((acc, n) => acc.split(n).join(''), line);
      if (ownerWords.test(rest)) continue; // "王工说用户该换卡" stays
      bad.push(line);
    }
    return bad;
  }

  function stripForeignSubjects(doc) {
    const bad = foreignSubjectLines(doc);
    if (!bad.length) return { doc: String(doc), removed: 0 };
    const set = new Set(bad);
    const kept = String(doc).split('\n').filter((l) => !set.has(l));
    return { doc: kept.join('\n'), removed: bad.length };
  }

  /**
   * Run all three in order. Structure is judged twice: once before the strips
   * (so a caller can retry the model) and once after (so a strip that emptied a
   * required section is visible).
   */
  function run({ text, material }) {
    const before = checkStructure(text);
    const bad = untraceable(text, material);
    const trace = stripGuardLines(text, bad);
    const subject = stripForeignSubjects(trace.doc);
    const after = checkStructure(subject.doc);
    return {
      ok: before.ok && after.ok,
      missing: before.missing,
      missingAfter: after.missing,
      untraceable: bad,
      removedTrace: trace.removed,
      removedSubject: subject.removed,
      doc: subject.doc,
    };
  }

  return {
    sections,
    people,
    checkStructure,
    untraceable,
    stripGuardLines,
    foreignSubjectLines,
    stripForeignSubjects,
    run,
  };
}
