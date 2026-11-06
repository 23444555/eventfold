// 冒烟：给种子事件后，块里必须真的出现那几段（且不给种子时结果不变）。
// 用法：node bench/seed-smoke.mjs --archive <archive.json> --src ../src/archive.mjs
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';

const argv = process.argv.slice(2);
const arg = (n, d) => {
  const i = argv.indexOf(`--${n}`);
  return i >= 0 ? argv[i + 1] : d;
};
const archivePath = arg('archive', 'examples/data/archive.json');
const srcPath = arg('src', '../src/archive.mjs');
process.env.MEMORY_ARCHIVE_JSON = archivePath;
const mod = await import(pathToFileURL(srcPath).href);

const snap = JSON.parse(readFileSync(archivePath, 'utf8'));
const events = snap.events || [];
const q = arg('q', '那次缓存阈值最后回到多少？');
const ids = (arg('ids', '') || '').split(',').filter(Boolean);
const seeded = ids.length ? ids : events.slice(0, 2).map((e) => e.id);

const plain = mod.archiveBlock(q, {});
const withSeed = mod.archiveBlock(q, { seedEvents: seeded });
const again = mod.archiveBlock(q, {});
const missing = seeded.filter((id) => {
  const ev = events.find((e) => String(e.id) === String(id));
  if (!ev) return true;
  return !(ev.facts || []).some((f) => withSeed.includes(String(f.t).trim().replace(/\s*\|\s*When:.*$/i, '')));
});

const out = {
  query: q,
  seeds: seeded,
  plainChars: plain.length,
  seedChars: withSeed.length,
  plainStable: plain === again,
  seedsPresent: seeded.length - missing.length,
  missing,
  ok: plain === again && missing.length === 0,
};
console.log(JSON.stringify(out, null, 2));
process.exit(out.ok ? 0 : 1);
