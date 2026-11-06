import test from 'node:test';
import assert from 'node:assert/strict';
import {
  checkStructure,
  untraceable,
  stripForeignSubjects,
  foreignSubjectLines,
} from '../src/guard.mjs';

const GOOD = `# 硬件与工位
> 一句话：一张 4070 Ti Super 顶着。

## 当前状态
- 现有显卡为 4070 Ti Super。
- 机箱因静电火花快要报废。

## 时间线
- 2026-09-01 · 换了 4070 Ti Super。

## 待跟进
- 明年换 4090。

## 已变化
- 无`;

test('structure: sections present', () => {
  assert.equal(checkStructure(GOOD).ok, true);
});

test('structure: missing one-liner is reported', () => {
  const bad = GOOD.replace('> 一句话：一张 4070 Ti Super 顶着。', '');
  assert.ok(checkStructure(bad).missing.includes('> 一句话：'));
});

test('structure: missing section is reported', () => {
  const bad = GOOD.replace('## 待跟进', '## 其它');
  const r = checkStructure(bad);
  assert.equal(r.ok, false);
  assert.ok(r.missing.includes('## 待跟进'));
});

test('provenance: traceable model passes, invented one fails', () => {
  const material = '- 现有显卡为 4070 Ti Super。\n- 明年换 4090。';
  assert.deepEqual(untraceable(GOOD, material), []);
  const invented = GOOD.replace('4090', '4090 Ti');
  assert.ok(untraceable(invented, material).includes('4090 Ti'));
});

test('subject: a bullet about someone else is dropped', () => {
  const doc = `${GOOD}\n- 李四买了静电容键盘，花了 2800。`;
  assert.equal(foreignSubjectLines(doc).length, 1);
  const { doc: fixed, removed } = stripForeignSubjects(doc);
  assert.equal(removed, 1);
  assert.ok(!fixed.includes('李四'));
});

test('subject: mention of another person WITH the owner stays', () => {
  const doc = `${GOOD}\n- 王工说用户该换 4090 了。`;
  assert.equal(foreignSubjectLines(doc).length, 0);
});
