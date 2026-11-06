// The English pack is only a solution if it routes. This test builds a tiny
// English archive, points the runtime at it, and asks a question the way an
// English user would — including a date written as "Nov 5".

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'eventfold-lang-'));
const archive = {
  version: 1,
  built: '2026-11-06',
  events: [
    {
      id: 'ev-1',
      title: 'NVMe disk replacement',
      status: 'done',
      first: '2026-11-05',
      last: '2026-11-06',
      summary: 'disk 5 failed and was replaced under warranty; the rebuild took 26 hours.',
      slots: [{ n: 'device', v: 'NVMe disk 5' }],
      facts: [
        { i: 1, d: '2026-11-05', t: 'The NVMe disk 5 failed and was replaced under warranty.' },
        { i: 2, d: '2026-11-06', t: 'The rebuild finished after 26 hours and the array reports 15.1 TB used.' },
      ],
      n: 2,
    },
    {
      id: 'ev-2',
      title: 'Cat food switch',
      status: 'done',
      first: '2026-10-01',
      last: '2026-10-01',
      summary: 'switched the cat to a new brand of wet food.',
      slots: [{ n: 'brand', v: 'Purina' }],
      facts: [{ i: 1, d: '2026-10-01', t: 'Switched the cat to Purina wet food.' }],
      n: 1,
    },
    {
      id: 'ev-3',
      title: 'Deployment rollback',
      status: 'done',
      first: '2026-11-02',
      last: '2026-11-02',
      summary: 'rolled back the release because the cache latency doubled.',
      slots: [{ n: 'service', v: 'checkout' }],
      facts: [{ i: 1, d: '2026-11-02', t: 'Rolled back the checkout release; p95 cache latency had doubled.' }],
      n: 1,
    },
  ],
};

const file = join(dir, 'archive.json');
writeFileSync(file, JSON.stringify(archive));
process.env.MEMORY_ARCHIVE_JSON = file;
process.env.MEMORY_ARCHIVE_LANG = 'en';

const { routeEvents, archiveBlock, getLang, activeLang } = await import('../src/archive.mjs');

test('activeLang() follows MEMORY_ARCHIVE_LANG', () => {
  assert.equal(activeLang().name, 'en');
  assert.equal(getLang('zh').name, 'zh');
});

test('dates: English forms and the Chinese default both parse', () => {
  assert.deepEqual(getLang('en').parseDate('what happened on Nov 5'), [11, 5]);
  assert.deepEqual(getLang('en').parseDate('2026-11-05'), [11, 5]);
  assert.deepEqual(getLang('en').parseDate('5 November'), [11, 5]);
  assert.deepEqual(getLang('zh').parseDate('11月5日'), [11, 5]);
});

test('dates are stripped before scoring, in English too', () => {
  const s = getLang('en').stripDates('the disk failed on Nov 5, 2026-11-05');
  assert.ok(!/nov/i.test(s), s);
  assert.ok(!/2026-11-05/.test(s), s);
});

test('English stop words are filtered per token, not by substring', () => {
  const q = getLang('en').queryOnly('what happened to the disk in the array');
  assert.ok(!/\bwhat\b/.test(q), q);
  assert.ok(!/\bthe\b/.test(q), q);
  assert.ok(q.includes('disk'), q);
});

test('routeEvents() puts the disk episode first', () => {
  const ranked = routeEvents('what happened to the nvme disk on Nov 5', 3);
  assert.equal(ranked[0].e.id, 'ev-1');
});

test('archiveBlock() renders the dated fact for an English question', () => {
  const block = archiveBlock('what happened to the nvme disk on Nov 5', { limit: 8 });
  assert.ok(block.includes('26 hours'), block);
  assert.ok(block.includes('2026-11-06'), block);
});
