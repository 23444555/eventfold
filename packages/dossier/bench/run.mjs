#!/usr/bin/env node
// Four-variant ablation, the same shape used to justify the design:
//
//   none       no dossier at all        -> the model should refuse
//   relevant   only the matching topic  -> the production path
//   all        every topic              -> does extra context help? (it does not)
//   stale      previous revision (.bak) -> is freshness measurable?
//
// Usage: node bench/run.mjs [--dir .] [--config dossier.config.json]

import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';
import { chat } from '../src/llm.mjs';
import { matchTopics } from '../src/inject.mjs';

const argv = process.argv.slice(2);
const flag = (n, d) => {
  const i = argv.indexOf(`--${n}`);
  if (i < 0) return d;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
};

const DIR = path.resolve(flag('dir', '.'));
const CONFIG = path.resolve(flag('config', path.join(DIR, 'dossier.config.json')));
const HERE = path.dirname(fileURLToPath(import.meta.url));
const QUESTIONS = flag('questions',
  existsSync(path.join(DIR, 'bench', 'questions.json'))
    ? path.join(DIR, 'bench', 'questions.json')
    : path.join(HERE, 'questions.json'));

const norm = (s) => String(s).toLowerCase().replace(/[\s，。、；：！？“”"'’‘（）()·\-—]/g, '');

function score(answer, anchors) {
  const a = norm(answer);
  return anchors.every((x) => a.includes(norm(x)));
}

async function readIfExists(p) {
  try {
    return await fs.readFile(p, 'utf8');
  } catch {
    return '';
  }
}

async function main() {
  const config = JSON.parse(await fs.readFile(CONFIG, 'utf8'));
  const questions = JSON.parse(await fs.readFile(QUESTIONS, 'utf8'));
  const docs = {};
  for (const t of config.topics) docs[t.id] = await readIfExists(path.join(DIR, t.file));

  const variants = ['none', 'relevant', 'all', 'stale'];
  const rows = [];
  for (const q of questions) {
    for (const variant of variants) {
      let material = '';
      if (variant === 'relevant') {
        const top = matchTopics(q.question, config.topics)[0];
        material = top ? docs[top.topic.id] : '';
      } else if (variant === 'all') {
        material = config.topics.map((t) => docs[t.id]).join('\n\n');
      } else if (variant === 'stale') {
        const top = matchTopics(q.question, config.topics)[0];
        material = top ? await readIfExists(path.join(DIR, `${top.topic.file}.bak`)) : '';
      }
      if (variant !== 'none' && !material.trim()) continue;

      const prompt = material
        ? `【档案】\n${material}\n\n【问题】${q.question}\n只依据档案回答；档案里没有就说「档案里没有」。`
        : `【问题】${q.question}\n没有档案可查，请直接说「档案里没有」。`;
      const res = await chat({ llm: config.llm, system: '你只依据给定档案回答问题，不补充档案之外的信息。', user: prompt, maxTokens: 300 });
      const ok = score(res.text, q.anchors);
      rows.push({ qid: q.id, topic: q.topic, variant, ok });
      process.stdout.write(`${variant.padEnd(9)} ${q.id.padEnd(4)} ${ok ? 'PASS' : 'fail'}\n`);
    }
  }

  const summary = variants.map((v) => {
    const rs = rows.filter((r) => r.variant === v);
    return { variant: v, pass: rs.filter((r) => r.ok).length, total: rs.length };
  });
  console.log('\n== summary');
  for (const s of summary) console.log(`  ${s.variant.padEnd(9)} ${s.pass}/${s.total}`);
  const outPath = path.join(DIR, 'bench', 'last-run.json');
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  await fs.writeFile(outPath, JSON.stringify({ at: new Date().toISOString(), summary, rows }, null, 2));
}

main().catch((e) => {
  console.error(`bench: ${e.message}`);
  process.exit(1);
});
