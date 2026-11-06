// A/B：事件档案层 vs 纯事实召回，同一批题、同一套机械判分。
//
// 三个臂：
//   episode      档案块（archiveBlock 默认预算）+ 常规 8 条事实召回   ← 线上真实形态
//   flat8        只有 8 条事实召回（多数记忆系统的返回形态）          ← 基线 1
//   flatBudget   同样 8 条起、继续往下填，直到字数花完 episode 的预算  ← 基线 2（更硬的对手）
//
// 判分：每道多腿题的「必备证据集」（required-sets.json 的 legs）。
//   每个 leg 是一组等价事实 id，块里出现其中任意一条即算这条腿齐。
//   严格口径 = 归一化文本完全包含；近似口径 = 字符 bigram Jaccard ≥ 0.55。
// 判分只看「证据在不在块里」，不看模型答得对不对——所以不花钱、可复现。
//
// 用法：
//   node bench/ab-coverage.mjs --qa novels/qa-v2.json --req novels/required-sets.json \
//        --archive p0-archive/archive.json --src ../src/archive.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

function arg(name, def) {
  const i = process.argv.indexOf(name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const QA = resolve(arg('--qa', join(HERE, '..', '..', 'novels', 'qa-v2.json')));
const REQ = resolve(arg('--req', join(HERE, '..', '..', 'novels', 'required-sets.json')));
const ARCHIVE = resolve(arg('--archive', join(HERE, '..', '..', 'p0-archive', 'archive.json')));
const OUT = arg('--out', '');
const NEAR = Number(arg('--near', 0.55));
const FLAT_BASE = Number(arg('--flat', 8));

// archive.mjs 在 import 时就读数据，所以先设环境变量再动态 import
process.env.MEMORY_ARCHIVE_JSON = ARCHIVE;
const srcPath = resolve(arg('--src', join(HERE, '..', 'src', 'archive.mjs')));
const { archiveBlock, flatFacts } = await import(pathToFileURL(srcPath).href);

const qa = JSON.parse(readFileSync(QA, 'utf8')).questions;
const req = JSON.parse(readFileSync(REQ, 'utf8'));
const data = JSON.parse(readFileSync(ARCHIVE, 'utf8'));

const norm = (s) => String(s || '').replace(/\s*\|\s*When:.*$/i, '').trim();
const bigrams = (s) => {
  const z = norm(s).replace(/[\s，。、；：]/g, '');
  const out = new Set();
  for (let i = 0; i < z.length - 1; i++) out.add(z.slice(i, i + 2));
  return out;
};
const jac = (a, b) => {
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  return inter / (a.size + b.size - inter || 1);
};

const textOf = new Map();
for (const e of data.events) for (const f of e.facts) textOf.set(f.i, f.t);

// 从档案块里抽出「真的铺进来的事实行」
function pickedFromBlock(blk) {
  const out = [];
  for (const line of blk.split('\n')) {
    const m = line.match(/^- (\d{4}-\d{2}-\d{2}) (.*)$/);
    if (m) out.push(norm(m[2]));
  }
  return out;
}

function score(spec, picked) {
  const pg = picked.map(bigrams);
  const near = (id) => {
    const g = bigrams(textOf.get(id) || '');
    return g.size > 0 && pg.some((p) => jac(g, p) >= NEAR);
  };
  let strict = 0;
  let approx = 0;
  const missing = [];
  for (const leg of spec.legs) {
    const exact = leg.some((i) => picked.includes(norm(textOf.get(i) || '')));
    if (exact) strict++;
    if (exact || leg.some((i) => near(i))) approx++;
    else missing.push(leg[0]);
  }
  return { strict, approx, legs: spec.legs.length, missing };
}

const arms = {
  episode: { rows: [], full: 0, near: 0, chars: 0, facts: 0 },
  flat8: { rows: [], full: 0, near: 0, chars: 0, facts: 0 },
  flatBudget: { rows: [], full: 0, near: 0, chars: 0, facts: 0 },
};

let n = 0;
for (const [qid, spec] of Object.entries(req)) {
  if (!spec || !spec.legs) continue;
  const q = qa.find((x) => x.id === qid);
  if (!q) continue;
  n++;

  const blk = archiveBlock(q.q, {}) || '';
  const flat = flatFacts(q.q, FLAT_BASE);
  const flatLines = flat.map((f) => `- ${f.d} ${norm(f.t)}`);

  // 臂 1：档案块 + 8 条事实
  const epPicked = [...pickedFromBlock(blk), ...flat.map((f) => norm(f.t))];
  const epChars = blk.length + flatLines.join('\n').length;
  const epScore = score(spec, epPicked);

  // 臂 2：只有 8 条事实
  const f8Picked = flat.map((f) => norm(f.t));
  const f8Chars = flatLines.join('\n').length;
  const f8Score = score(spec, f8Picked);

  // 臂 3：8 条起，按字数预算继续往下填（最多 80 条）
  const pool = flatFacts(q.q, 80);
  const fbLines = [];
  let fbChars = 0;
  for (const f of pool) {
    const line = `- ${f.d} ${norm(f.t)}`;
    if (fbChars + line.length + 1 > epChars) break;
    fbLines.push(line);
    fbChars += line.length + 1;
  }
  const fbScore = score(spec, fbLines.map((l) => l.replace(/^- \d{4}-\d{2}-\d{2} /, '')));

  for (const [name, s, chars, cnt] of [
    ['episode', epScore, epChars, epPicked.length],
    ['flat8', f8Score, f8Chars, f8Picked.length],
    ['flatBudget', fbScore, fbChars, fbLines.length],
  ]) {
    const a = arms[name];
    a.rows.push({ qid, ...s, chars, facts: cnt });
    if (s.strict === s.legs) a.full++;
    if (s.approx === s.legs) a.near++;
    a.chars += chars;
    a.facts += cnt;
  }
}

const pct = (x, d) => (100 * x / (d || 1)).toFixed(1) + '%';
const line = (name, a) =>
  `| ${name} | ${a.full}/${n} (${pct(a.full, n)}) | ${a.near}/${n} (${pct(a.near, n)}) | ${Math.round(a.chars / n)} | ${(a.facts / n).toFixed(1)} | ${Math.round((a.chars / n) * 0.65)} |`;

console.log(`题数 ${n}（多腿题）｜数据 ${data.events.length} 事件 / ${textOf.size} 条事实｜近似阈值 J≥${NEAR}\n`);
console.log('| 方案 | 全铺齐（严格） | 全铺齐（近似） | 平均字数 | 平均条数 | 估算 token |');
console.log('|---|---|---|---|---|---|');
for (const [name, a] of Object.entries(arms)) console.log(line(name, a));

const label = { episode: '档案+召回', flat8: '纯 8 条召回', flatBudget: '等预算纯召回' };
console.log('\n逐题（严格口径，腿数齐/总）：');
for (let i = 0; i < n; i++) {
  const row = arms.episode.rows[i];
  const q = qa.find((x) => x.id === row.qid);
  console.log(
    `${row.qid.padEnd(6)} ${String(q.q).slice(0, 26).padEnd(28)} ` +
      Object.entries(arms)
        .map(([k, a]) => `${label[k]} ${a.rows[i].strict}/${a.rows[i].legs}`)
        .join('  '),
  );
}

if (OUT) {
  writeFileSync(
    OUT,
    JSON.stringify({ n, near: NEAR, arms: Object.fromEntries(Object.entries(arms).map(([k, a]) => [k, { full: a.full, near: a.near, avgChars: Math.round(a.chars / n), rows: a.rows }])) }, null, 2),
    'utf8',
  );
  console.log(`\n写出 ${OUT}`);
}
