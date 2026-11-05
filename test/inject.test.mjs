import test from 'node:test';
import assert from 'node:assert/strict';
import { matchTopics, renderExcerpt } from '../src/inject.mjs';

const topics = [
  { id: 'hardware', keywords: ['显卡', '机箱', 'A100'] },
  { id: 'memory_system', keywords: ['记忆', '召回', 'BM25'] },
];

test('inject: picks the topic whose keywords appear', () => {
  assert.equal(matchTopics('我显卡什么型号', topics)[0].topic.id, 'hardware');
  assert.equal(matchTopics('召回率怎么提', topics)[0].topic.id, 'memory_system');
});

test('inject: no hit means no injection', () => {
  assert.equal(matchTopics('今天吃什么', topics).length, 0);
});

test('inject: excerpt keeps at most 5 status bullets and is capped', () => {
  const doc = `# T
> 一句话：一句话。

## 当前状态
- a1
- a2
- a3
- a4
- a5
- a6
- a7

## 时间线
- 不该出现的行

## 待跟进
- t1
- t2
- t3
- t4`;
  const out = renderExcerpt(doc, 600);
  assert.ok(out.includes('- a5'));
  assert.ok(!out.includes('- a6'));
  assert.ok(out.includes('- t3'));
  assert.ok(!out.includes('- t4'));
  assert.ok(!out.includes('不该出现的行'));
  assert.ok(out.length <= 600);
});

test('inject: cap is enforced', () => {
  const doc = `# T\n\n## 当前状态\n${'- x\n'.repeat(200)}`;
  assert.ok(renderExcerpt(doc, 120).length <= 120);
});
