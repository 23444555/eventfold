// Source adapter: extracted facts (already-distilled sentences with a date).
//
// Fact shape (see examples/facts.sample.json):
//   { id, text, created_at: "YYYY-MM-DD", fact_type: "world" }

import { withinWindow } from './archive.mjs';

export async function load(topic, { readJson }) {
  const facts = await readJson(topic.facts || 'examples/facts.sample.json');
  const items = Array.isArray(facts) ? facts : facts.items || [];
  const windowDays = topic.window_days ?? 400;
  const types = topic.fact_types || [];

  return items
    .filter((f) => !types.length || types.includes(f.fact_type || 'world'))
    .filter((f) => withinWindow(f.created_at, windowDays))
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
    .map((f) => ({
      key: `fact:${f.id}`,
      date: f.created_at,
      text: String(f.text || '').trim(),
    }));
}
