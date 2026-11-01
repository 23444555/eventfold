// 压测用（v2，激进版）：把 44 道题的「原问 + 全部改写变体」都变成实例，
// 每个实例备齐四个纯文本臂，向量/BM25 臂交给 ab-arms.py。
//
// 实例 = （问句，腿集）。原问 44 个 + 改写变体 68 个 = 112 个实例，腿 62 条（每条腿按实例重复计分）。
//
// 产出 bench/ab-gen.json：
//   instances[] { iid, baseId, kind(base|variant), q, legs, epChars,
//                 episode[], episodeChars, noBoost[], noBoostChars,
//                 flat8[], flatBudget[], blockLines, blockChars }
//   corpus[]    { i, d, t }
//   lat         { boostOn:{...}, boostOff:{...} }
//
// 用法：node bench/ab-gen.mjs [--reps 10]
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const arg = (n, d) => {
  const i = process.argv.indexOf(n);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d;
};

const QA = resolve(arg('--qa', join(HERE, '..', '..', 'novels', 'qa-v2.json')));
const REQ = resolve(arg('--req', join(HERE, '..', '..', 'novels', 'required-sets.json')));
const VAR = resolve(arg('--variants', join(HERE, '..', '..', 'novels', 'variants-3-src.json')));
const ARCHIVE = resolve(arg('--archive', join(HERE, '..', '..', 'p0-archive', 'archive.json')));
const SRC = resolve(arg('--src', join(HERE, '..', 'src', 'archive.mjs')));
const OUT = resolve(arg('--out', join(HERE, 'ab-gen.json')));
const REPS = Number(arg('--reps', 10));
const FLAT = Number(arg('--flat', 8));

process.env.MEMORY_ARCHIVE_JSON = ARCHIVE;
const { archiveBlock, flatFacts } = await import(pathToFileURL(SRC).href);

const qa = JSON.parse(readFileSync(QA, 'utf8')).questions;
const req = JSON.parse(readFileSync(REQ, 'utf8'));
const variants = JSON.parse(readFileSync(VAR, 'utf8'));
const data = JSON.parse(readFileSync(ARCHIVE, 'utf8'));

const norm = (s) => String(s || '').replace(/\s*\|\s*When:.*$/i, '').trim();
const byId = new Map();
for (const e of data.events) for (const f of e.facts) byId.set(f.i, f);

function pickedFromBlock(blk) {
  const out = [];
  for (const line of blk.split('\n')) {
    const m = line.match(/^- (\d{4}-\d{2}-\d{2}) (.*)$/);
    if (m) out.push(norm(m[2]));
  }
  return out;
}

// 实例清单
const inst = [];
for (const q of qa) {
  if (!q.anchor_unit) continue;
  const spec = req[q.id];
  const legs = spec && spec.legs ? spec.legs : [[q.anchor_unit]];
  inst.push({ iid: q.id, baseId: q.id, kind: 'base', q: q.q, type: q.type, legs });
  const g = variants.find((x) => x.id === q.id);
  if (g) (g.variants || []).forEach((v, k) => {
    if (v && String(v).trim()) inst.push({ iid: `${q.id}#v${k + 1}`, baseId: q.id, kind: 'variant', q: String(v), type: q.type, legs });
  });
}

const latBoost = [];
const latNo = [];
const out = [];

function block(text, boost) {
  process.env.ARCHIVE_BOOST = boost ? 'on' : 'off';
  return archiveBlock(text, {}) || '';
}

for (const it of inst) {
  const t0 = process.hrtime.bigint();
  const blk = block(it.q, true);
  latBoost.push(Number(process.hrtime.bigint() - t0) / 1e6);
  for (let r = 1; r < REPS; r++) {
    const a = process.hrtime.bigint();
    block(it.q, true);
    latBoost.push(Number(process.hrtime.bigint() - a) / 1e6);
  }
  const nb = block(it.q, false);
  const t1 = process.hrtime.bigint();
  block(it.q, false);
  latNo.push(Number(process.hrtime.bigint() - t1) / 1e6);

  const flat = flatFacts(it.q, FLAT);
  const flat8 = flat.map((f) => norm(f.t));
  const epChars = blk.length + flat8.map((t) => t.length + 12).reduce((a, b) => a + b, 0);

  const budget = (lines) => {
    const keep = [];
    let used = 0;
    for (const [d, t] of lines) {
      const line = `${d} ${t}`;
      if (used + line.length + 1 > epChars) break;
      keep.push(line);
      used += line.length + 1;
    }
    return keep;
  };
  const pool = flatFacts(it.q, 80).map((f) => [f.d, norm(f.t)]);
  const noBoostLines = pickedFromBlock(nb);

  out.push({
    iid: it.iid,
    baseId: it.baseId,
    kind: it.kind,
    q: it.q,
    type: it.type,
    legs: it.legs,
    epChars,
    blockChars: blk.length,
    blockLines: pickedFromBlock(blk).length,
    episode: pickedFromBlock(blk),
    flat8,
    flatBudget: budget(pool),
    noBoost: noBoostLines,
    noBoostChars: nb.length + flat8.map((t) => t.length + 12).reduce((a, b) => a + b, 0),
  });
}

const corpus = [];
for (const e of data.events) for (const f of e.facts) corpus.push({ i: f.i, d: f.d, t: norm(f.t) });

const stat = (arr) => {
  const s = [...arr].sort((a, b) => a - b);
  const p = (q) => Math.round(s[Math.min(s.length - 1, Math.floor(s.length * q))] * 100) / 100;
  return { runs: s.length, p50: p(0.5), p95: p(0.95), max: Math.round(s[s.length - 1] * 100) / 100, mean: Math.round((s.reduce((a, b) => a + b, 0) / s.length) * 100) / 100 };
};

const lat = { boostOn: stat(latBoost), boostOff: stat(latNo) };
writeFileSync(OUT, JSON.stringify({ instances: out, corpus, lat }, null, 1), 'utf8');

const nb = out.filter((x) => x.kind === 'base').length;
const nv = out.length - nb;
const legs = out.reduce((a, x) => a + x.legs.length, 0);
console.log(`实例 ${out.length}（原问 ${nb} + 改写 ${nv}）｜腿次 ${legs}｜事实 ${corpus.length}`);
console.log(`archiveBlock p50 ${lat.boostOn.p50} ms / p95 ${lat.boostOn.p95} ms（关 boost：p50 ${lat.boostOff.p50}）`);
console.log(`平均档案块 ${Math.round(out.reduce((a, x) => a + x.blockChars, 0) / out.length)} 字 / ${(out.reduce((a, x) => a + x.blockLines, 0) / out.length).toFixed(1)} 行`);
console.log(`写出 ${OUT}`);
