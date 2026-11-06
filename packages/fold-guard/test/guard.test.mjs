import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createGuard, DEFAULT_EXEMPT } from '../src/index.mjs';

const guard = createGuard({
  sections: ['当前状态', '时间线', '待跟进', '已变化'],
  people: ['李总', '王工', '老周'],
});

const doc = (body) => `# 硬件与工位

> 一句话：主力机是 4070 Ti Super。

## 当前状态
${body}

## 时间线
- 2026-10-03 换显卡

## 待跟进
- 显示器还没换

## 已变化
- 无
`;

test('structure: accepts a complete document', () => {
  const r = guard.checkStructure(doc('- 显卡 4070 Ti Super'));
  assert.equal(r.ok, true);
  assert.deepEqual(r.missing, []);
});

test('structure: names every missing section', () => {
  const r = guard.checkStructure('# 标题\n\n## 当前状态\n正文');
  assert.equal(r.ok, false);
  assert.ok(r.missing.includes('> 一句话：'));
  assert.ok(r.missing.includes('## 时间线'));
});

test('provenance: a number absent from the material is flagged', () => {
  const bad = guard.untraceable('显卡是 4090，显存 24GB', '显卡是 4070 Ti Super，显存 16GB');
  assert.ok(bad.includes('4090'));
  assert.ok(bad.includes('24GB'));
  assert.ok(!bad.includes('4070'));
});

test('provenance: dates and small ordinals are exempt', () => {
  const material = '2026-10-03 换了显卡，前后对比了 3 次';
  const bad = guard.untraceable('2026-10-03 换的，第 2 次开机就没问题', material);
  assert.deepEqual(bad, []);
});

test('provenance: whitelist lets domain words through', () => {
  const g = createGuard({ whitelist: ['TTL'] });
  assert.deepEqual(g.untraceable('TTL 设为 3600 秒', '缓存 3600 秒过期'), []);
  assert.deepEqual(guard.untraceable('TTL 设为 3600 秒', '缓存 3600 秒过期'), ['TTL']);
});

test('provenance: offending bullets are dropped, prose is kept', () => {
  const text = doc('- 显卡是 4090\n- 显示器还是旧的');
  const bad = guard.untraceable(text, '显卡是 4070 Ti Super');
  const r = guard.stripGuardLines(text, bad);
  assert.equal(r.removed, 1);
  assert.ok(!r.doc.includes('4090'));
  assert.ok(r.doc.includes('显示器还是旧的'));
});

test('subject: a bullet about someone else is dropped', () => {
  const text = doc('- 李总把显卡换成了 4090\n- 用户自己用的是 4070 Ti Super');
  const lines = guard.foreignSubjectLines(text);
  assert.equal(lines.length, 1);
  assert.ok(lines[0].includes('李总'));
});

test('subject: mentioning someone is fine when the owner is the subject', () => {
  const text = doc('- 王工说用户该换显示器了');
  assert.deepEqual(guard.foreignSubjectLines(text), []);
});

test('run: reports both strips and the structure verdict', () => {
  const text = doc('- 显卡是 4090\n- 李总把显示器换成了艺卓');
  const r = guard.run({ text, material: '显卡是 4070 Ti Super' });
  assert.equal(r.ok, true);
  assert.equal(r.removedTrace, 1);
  assert.equal(r.removedSubject, 1);
  assert.ok(!r.doc.includes('4090'));
  assert.ok(!r.doc.includes('李总'));
});

test('exempt list is exported so callers can extend it', () => {
  assert.ok(DEFAULT_EXEMPT.some((re) => re.test('2026-11-05')));
});
