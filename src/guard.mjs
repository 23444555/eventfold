// Guards: what keeps a generated dossier from sounding like a detective report
// about other people. Three of them, in the order they run:
//
//   1. structure   — the file must have the sections the injector expects
//   2. provenance  — every number / model name must exist verbatim in the material
//   3. subject     — a bullet about someone else (no owner word in the sentence) is dropped
//
// All three fail *closed*: when in doubt, drop the line. A dossier that says less
// is fine; a dossier that says the wrong person's GPU is not.

export const SECTIONS = ['当前状态', '时间线', '待跟进', '已变化'];

// Words that mean "this line is about the person we are writing for".
const OWNER_WORD = /(用户|号主|主人|本人|你|我)/;

// People who appear in group chats but are not the owner. Extend for your data.
// Kept in one place so it can be reviewed: this list is the guard's whole opinion
// about who matters.
export const OTHER_PEOPLE = [
  '李总', '张伟', '李四', '赵敏', '周杰', '王工', '老周', '张三', '王五',
  '郑凯', '张伟明', '小鹿', '阿茶', '唐果', '老吴', '小胖', '小陈', '老李',
];

export function checkStructure(text) {
  const body = String(text || '');
  const missing = [];
  if (!/^\s*#\s+\S+/m.test(body)) missing.push('# 标题');
  if (!/^\s*>\s*一句话[:：]\s*\S+/m.test(body)) missing.push('> 一句话：');
  for (const s of SECTIONS) {
    if (!body.includes(`## ${s}`)) missing.push(`## ${s}`);
  }
  return { ok: missing.length === 0, missing };
}

// Numbers, model names, version-ish tokens: things a reader will quote back at you.
const TRACE_TOKEN = /[A-Za-z0-9][A-Za-z0-9.+\-]{1,}(?:\s?[A-Za-z0-9.+\-]+)?|\d[\d,.]*/g;

export function untraceable(text, material) {
  const hay = String(material || '');
  const seen = new Set();
  const bad = [];
  for (const m of String(text || '').matchAll(TRACE_TOKEN)) {
    const tok = m[0].trim();
    if (tok.length < 2) continue;
    if (/^\d{1,2}$/.test(tok)) continue;          // small ordinals are safe
    if (/^\d{4}-\d{2}-\d{2}$/.test(tok)) continue; // dates are structure, not claims
    if (seen.has(tok)) continue;
    seen.add(tok);
    if (!hay.includes(tok)) bad.push(tok);
  }
  return bad;
}

export function stripGuardLines(doc, bad) {
  const set = new Set(bad.map((b) => String(b)));
  const lines = String(doc).split('\n');
  const kept = lines.filter((line) => {
    if (!/^\s*[-*]/.test(line)) return true;
    for (const b of set) if (line.includes(b)) return false;
    return true;
  });
  return { doc: kept.join('\n'), removed: lines.length - kept.length };
}

export function foreignSubjectLines(doc) {
  const bad = [];
  for (const line of String(doc).split('\n')) {
    if (!/^\s*[-*]/.test(line)) continue;
    const names = OTHER_PEOPLE.filter((n) => line.includes(n));
    if (!names.length) continue;
    const rest = names.reduce((acc, n) => acc.split(n).join(''), line);
    if (OWNER_WORD.test(rest)) continue;          // "王工说用户该换卡" stays
    bad.push(line);
  }
  return bad;
}

export function stripForeignSubjects(doc) {
  const bad = foreignSubjectLines(doc);
  if (!bad.length) return { doc: String(doc), removed: 0 };
  const set = new Set(bad);
  const kept = String(doc).split('\n').filter((l) => !set.has(l));
  return { doc: kept.join('\n'), removed: bad.length };
}
