// Build one dossier: pick material -> ask the model -> run three guards -> write.
//
// The guards are not decoration. On the corpus this was extracted from, 45% of the
// bullets in two topics were about *other* people (a colleague's GPU purchase, a
// neighbour's cache TTL). Numbers were traceable, subjects were not. So:
//   guard 2 drops bullets with untraceable numbers,
//   guard 3 drops bullets whose subject is someone else,
// and the file records `guard` so drift is visible over time.

import fs from 'node:fs/promises';
import path from 'node:path';
import { chat } from './llm.mjs';
import {
  checkStructure,
  untraceable,
  stripGuardLines,
  stripForeignSubjects,
} from './guard.mjs';
import * as archiveSource from './sources/archive.mjs';
import * as factsSource from './sources/facts.mjs';
import { resolveFrom, readJson, stamp, exists } from './util.mjs';

export const SYSTEM = `你在为一个人整理「档案」：把散落的对话事件压成几段可复用的自我简介。
只使用给定素材，不补充常识，不猜测。素材里没有的，就不写。

硬性要求：
1. 结构固定：一级标题、一句话、## 当前状态、## 时间线、## 待跟进、## 已变化。
2. 数字、型号、版本号必须逐字照抄素材，不许换算、不许四舍五入（比如 4070 Ti Super 不能写成 4090）。
3. 只写「用户/号主/本人」这个人的事。素材里如果主语是别人（同事、群友），不要写进档案。
4. 陈述事实，不写建议、不写评价、不写「可能」「大概」。
5. 每条 bullet 一句话，不超过 60 字。时间线用「YYYY-MM-DD · 事件」。
6. 素材不足以支撑某一节时，写「无」。
7. 标题下一行必须是一句话摘要，格式严格为：> 一句话：<不超过 40 字>

模板（照这个骨架写，不要增删标题）：
# <主题名>
> 一句话：<摘要>

## 当前状态
- ...

## 时间线
- YYYY-MM-DD · ...

## 待跟进
- ...

## 已变化
- ...

只输出 markdown 正文，不要代码块包裹。`;

const SOURCES = { archive: archiveSource, facts: factsSource };

export async function selectMaterial(topic, baseDir) {
  const adapter = SOURCES[topic.source || 'archive'];
  if (!adapter) throw new Error(`未知素材源：${topic.source}（可选 archive / facts）`);
  const items = await adapter.load(topic, {
    readJson: (p) => readJson(resolveFrom(baseDir, p)),
  });
  const max = topic.max_items ?? 15;
  return items.slice(-max);
}

export function materialText(items) {
  return items.map((it) => `- [${it.date}] ${it.text}`).join('\n');
}

export function buildPrompt(topic, items, previous) {
  const mat = materialText(items);
  const prev = previous ? `\n\n【上一版档案（仅供参考语气，不要照抄过时内容）】\n${previous.slice(0, 1200)}` : '';
  return `主题：${topic.title}
分节提示：${topic.section_hint || '当前状态 / 时间线 / 待跟进'}
字数上限：${topic.max_chars ?? 3000} 字

【素材】
${mat}${prev}`;
}

export async function buildTopic(topic, { config, baseDir, dir, force = false, dry = false, log = () => {} }) {
  const items = await selectMaterial(topic, baseDir);
  const file = resolveFrom(dir, topic.file);
  const previous = (await exists(file)) ? await fs.readFile(file, 'utf8') : '';
  const prompt = buildPrompt(topic, items, previous);

  log(`[${topic.id}] 素材 ${items.length} 条 / ${prompt.length} 字`);
  if (dry) {
    log(`[${topic.id}] --dry：只打印 prompt，不调用模型\n${prompt.slice(0, 800)}`);
    return { topic: topic.id, items: items.length, dry: true };
  }
  if (!items.length && !force) {
    log(`[${topic.id}] 素材为空，跳过（要生成空档案加 --force）`);
    return { topic: topic.id, items: 0, skipped: true };
  }

  // One retry with the exact defect spelled out: cheaper than a looser validator,
  // and it keeps the file shape stable for the injector.
  let doc = '';
  let st = { ok: false, missing: [] };
  for (let attempt = 0; attempt < 2; attempt++) {
    const user = attempt === 0
      ? prompt
      : `${prompt}\n\n【上一次输出不合格】缺：${st.missing.join('、')}。请严格按模板重写，标题下一行必须是「> 一句话：…」。`;
    const res = await chat({ llm: config.llm, system: SYSTEM, user });
    doc = String(res.text || '').trim()
      .replace(/^```(?:markdown)?\n/, '')
      .replace(/\n```$/, '');
    st = checkStructure(doc);
    if (st.ok) break;
    log(`[${topic.id}] 结构不合格（第 ${attempt + 1} 次）：缺 ${st.missing.join('、')}`);
  }
  if (!st.ok) throw new Error(`结构不合格，缺：${st.missing.join('、')}`);

  let guardFailed = 0;

  // guard 2: provenance
  let bad = untraceable(doc, materialText(items) + previous);
  if (bad.length) {
    log(`[${topic.id}] 不可溯源：${bad.slice(0, 6).join(', ')}`);
    const cut = stripGuardLines(doc, bad);
    doc = cut.doc;
    guardFailed += cut.removed;
  }

  // guard 3: subject
  const subj = stripForeignSubjects(doc);
  if (subj.removed) {
    doc = subj.doc;
    guardFailed += subj.removed;
    log(`[${topic.id}] 删掉 ${subj.removed} 行主体是别人的`);
  }

  const header = `<!-- dossier:v1 id=${topic.id} built=${stamp()} items=${items.length} guard=${guardFailed} -->`;
  await fs.mkdir(path.dirname(file), { recursive: true });
  if (previous) await fs.writeFile(`${file}.bak`, previous, 'utf8');
  await fs.writeFile(file, `${header}\n${doc}\n`, 'utf8');
  log(`[${topic.id}] 写入 ${topic.file}（${doc.length} 字，guard ${guardFailed}）`);

  return { topic: topic.id, items: items.length, chars: doc.length, guard: guardFailed, keys: items.map((i) => i.key) };
}
