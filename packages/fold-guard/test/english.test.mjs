// English pack: the guards are language-agnostic once you hand them the language.
// This file is the executable form of docs/LANGUAGE.md — if it ever fails, the
// "English works with configuration" claim in the README is false.
//
// Every line here is traceable in `material` on purpose: the provenance guard runs
// first, and a line it drops never reaches the subject guard.

import test from 'node:test';
import assert from 'node:assert/strict';
import { createGuard, EN } from '../src/index.mjs';

const material = [
  '2026-04-12 the user replaced disk 5 in the array; the rebuild took 26 hours.',
  '2026-04-12 array now reports 15.1 TB used, up from 13.4 TB.',
  '2026-04-13 Marcus told the user to replace disk 5.',
  '2026-04-13 Marcus replaced his card with a 4090.',
].join('\n');

const digest = [
  '# Disk array',
  '',
  '> TL;DR: swapped disk 5, rebuild took 26 hours, now at 15.1 TB.',
  '',
  '## Status',
  '- The user replaced disk 5; rebuild took 26 hours and the array is at 15.1 TB.',
  '- Marcus told the user to replace disk 5.',
  '- Marcus replaced his card with an H100.',
].join('\n');

const en = () =>
  createGuard({
    sections: ['Status'],
    summary: EN.summary,
    labels: EN.labels,
    people: ['Marcus'],
    ownerWords: EN.ownerWords,
    token: EN.token,
  });

test('EN pack accepts an English summary line', () => {
  const r = en().checkStructure(digest);
  assert.equal(r.ok, true, r.missing.join(','));
});

test('the Chinese summary shape is not silently accepted by the EN pack', () => {
  const zhStyle = digest.replace('> TL;DR: swapped', '> 一句话：swapped');
  const r = en().checkStructure(zhStyle);
  assert.equal(r.ok, false);
  assert.ok(r.missing.includes(EN.labels.summary));
});

test('a line that mentions someone else but also the owner survives', () => {
  const r = en().run({ text: digest, material });
  assert.equal(r.removedSubject, 0);
  assert.ok(r.doc.includes('Marcus told the user'), r.doc);
});

test('the same line is dropped when owner words are not configured for English', () => {
  const g = createGuard({
    sections: ['Status'],
    summary: EN.summary,
    labels: EN.labels,
    people: ['Marcus'],
    token: EN.token,
  });
  const r = g.run({ text: digest, material });
  assert.equal(r.removedSubject, 1);
  assert.ok(!r.doc.includes('Marcus told the user'), r.doc);
});

test('provenance still catches an invented model name', () => {
  const r = en().run({ text: digest, material });
  assert.ok(r.untraceable.includes('H100'), JSON.stringify(r.untraceable));
  assert.ok(!r.doc.includes('H100'), r.doc);
});
