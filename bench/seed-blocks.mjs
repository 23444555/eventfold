// 给一批「已知种子事件」的问句批量生成档案块（给 ab-arms.py 的 episodeSeed 臂用）。
// 用法：node bench/seed-blocks.mjs --jobs jobs.json --out blocks.json
//       --archive ../../p0-archive/archive.json --src ../src/archive.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const arg = (n, d) => {
  const i = argv.indexOf(`--${n}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const JOBS = resolve(arg('jobs', join(HERE, 'seed-jobs.json')));
const OUT = resolve(arg('out', join(HERE, 'seed-blocks.json')));
const ARCHIVE = resolve(arg('archive', join(HERE, '..', '..', 'p0-archive', 'archive.json')));
const SRC = resolve(arg('src', join(HERE, '..', 'src', 'archive.mjs')));
const GATE = arg('seed-gate', '');
const SCORE = arg('seed-score', '');

process.env.MEMORY_ARCHIVE_JSON = ARCHIVE;
if (GATE !== '') process.env.ARCHIVE_SEED_GATE = String(GATE);
if (SCORE !== '') process.env.ARCHIVE_SEED_SCORE = String(SCORE);
const { archiveBlock } = await import(pathToFileURL(SRC).href);

const jobs = JSON.parse(readFileSync(JOBS, 'utf8'));
const out = {};
const t0 = Date.now();
for (const j of jobs.items) {
  out[j.iid] = archiveBlock(j.q, { seedEvents: j.seeds || [] }) || '';
}
writeFileSync(OUT, JSON.stringify(out), 'utf8');
const chars = Object.values(out).map((s) => s.length);
chars.sort((a, b) => a - b);
const p = (q) => chars[Math.min(chars.length - 1, Math.floor(chars.length * q))];
console.log(
  JSON.stringify(
    { items: jobs.items.length, out: OUT, p50Chars: p(0.5), p95Chars: p(0.95), elapsedMs: Date.now() - t0 },
    null,
    2,
  ),
);
