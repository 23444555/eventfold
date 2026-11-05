#!/usr/bin/env node
// dossier — turn conversation events into per-topic dossiers, inject only the one
// that matches. Commands: init / build / tick / list / inject.

import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { buildTopic } from './build.mjs';
import { tick } from './tick.mjs';
import { renderExcerpt, matchTopics } from './inject.mjs';
import { loadState, readJson, resolveFrom, exists } from './util.mjs';

const argv = process.argv.slice(2);
const cmd = argv[0] || 'help';
const flag = (name, def) => {
  const i = argv.indexOf(`--${name}`);
  if (i < 0) return def;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
};
const has = (name) => argv.includes(`--${name}`);

const CONFIG = path.resolve(flag('config', 'dossier.config.json'));
const BASE = path.dirname(CONFIG);
const DIR = path.resolve(flag('dir', BASE));
const STATE = path.join(DIR, 'dossier.state.json');

function log(msg) {
  process.stdout.write(`${msg}\n`);
}

async function loadConfig() {
  if (!(await exists(CONFIG))) {
    throw new Error(`找不到配置 ${CONFIG}。先跑：npx dossier init`);
  }
  const cfg = await readJson(CONFIG);
  if (!Array.isArray(cfg.topics) || !cfg.topics.length) throw new Error('配置里没有 topics');
  return cfg;
}

async function main() {
  if (cmd === 'help' || cmd === '--help' || cmd === '-h') {
    log(`dossier 0.1.0

  dossier init                      生成 dossier.config.json 与示例素材
  dossier build --all               重建全部主题
  dossier build --topic hardware    只重建一个
  dossier tick                      增量：有新素材才重建
  dossier list                      看主题与上一版信息
  dossier inject "我显卡什么型号"      按关键词挑一篇，输出 ≤600 字片段

选项：--config <path>  --dir <path>  --force  --dry  --chars <n>`);
    return;
  }

  if (cmd === 'init') {
    const cfgPath = CONFIG;
    if (await exists(cfgPath)) {
      log(`已存在 ${cfgPath}，没有覆盖。`);
      return;
    }
    const exampleCfg = path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..', 'examples', 'config.sample.json');
    await fs.mkdir(BASE, { recursive: true });
    await fs.copyFile(exampleCfg, cfgPath);
    for (const f of ['events.sample.json', 'facts.sample.json']) {
      const src = path.join(path.dirname(exampleCfg), f);
      const dst = path.join(BASE, f);
      if (!(await exists(dst))) await fs.copyFile(src, dst);
    }
    log(`已生成 ${cfgPath} 与示例素材。编辑 topics[].keywords 后跑：dossier build --all`);
    return;
  }

  const config = await loadConfig();

  if (cmd === 'list') {
    const state = await loadState(STATE);
    for (const t of config.topics) {
      const st = state.topics?.[t.id];
      log(`${t.id.padEnd(16)} ${t.title}  文件=${t.file}  上次=${st?.last_built_at ?? '从未'}  素材=${st?.last_items ?? 0}`);
    }
    return;
  }

  if (cmd === 'build') {
    const only = flag('topic', null);
    const targets = only ? config.topics.filter((t) => t.id === only) : config.topics;
    if (!targets.length) throw new Error(`没有这个主题：${only}`);
    for (const t of targets) {
      await buildTopic(t, { config, baseDir: BASE, dir: DIR, force: has('force'), dry: has('dry'), log });
    }
    return;
  }

  if (cmd === 'tick') {
    const out = await tick({ config, baseDir: BASE, dir: DIR, statePath: STATE, log, force: has('force') });
    log(JSON.stringify(out, null, 2));
    return;
  }

  if (cmd === 'inject') {
    const query = argv[1] && !argv[1].startsWith('--') ? argv[1] : '';
    if (!query) throw new Error('用法：dossier inject "问句"');
    const ranked = matchTopics(query, config.topics);
    if (!ranked.length) {
      log('（没有主题的关键词命中，不注入）');
      return;
    }
    const topic = ranked[0].topic;
    const file = resolveFrom(DIR, topic.file);
    const doc = (await exists(file)) ? await fs.readFile(file, 'utf8') : '';
    log(renderExcerpt(doc, Number(flag('chars', 600))));
    return;
  }

  throw new Error(`未知命令：${cmd}`);
}

main().catch((err) => {
  console.error(`dossier: ${err.message}`);
  process.exit(1);
});
