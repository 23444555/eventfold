// Incremental build: only rewrite a dossier when enough *new* material arrived.
//
// The cursor is the whole trick. Rebuilding on a timer without one costs money and
// makes the file churn; rebuilding only when new events appear keeps it tied to
// reality. `min_new_items` is the noise floor (default 1: any new event counts).

import { selectMaterial, buildTopic } from './build.mjs';
import { loadState, saveState, today } from './util.mjs';

function estimateCost(config, promptChars, maxOut) {
  const price = config.llm?.pricing;
  if (!price) return 0;
  const inTok = promptChars / 1.6;
  return (inTok / 1e6) * (price.in_per_m ?? 0) + (maxOut / 1e6) * (price.out_per_m ?? 0);
}

export async function tick({ config, baseDir, dir, statePath, log = () => {}, force = false }) {
  const state = await loadState(statePath);
  const day = today();
  if (state.daily?.date !== day) state.daily = { date: day, spent_rmb: 0 };

  const budget = config.budget?.daily_rmb ?? 0;
  const minNew = config.budget?.min_new_items ?? 1;
  const results = [];

  for (const topic of config.topics) {
    const st = (state.topics[topic.id] ||= { cursor: [], last_built_at: null, last_items: 0 });
    const items = await selectMaterial(topic, baseDir);
    const known = new Set(st.cursor || []);
    const fresh = items.filter((it) => !known.has(it.key));

    if (!fresh.length) {
      log(`[${topic.id}] 没有新素材，跳过`);
      results.push({ topic: topic.id, skipped: 'no-new', items: items.length });
      continue;
    }
    if (fresh.length < minNew && !force) {
      log(`[${topic.id}] 新增 ${fresh.length} 条 < min_new_items ${minNew}，跳过`);
      results.push({ topic: topic.id, skipped: 'below-min', fresh: fresh.length });
      continue;
    }

    const est = estimateCost(config, (topic.max_chars ?? 3000) * 2, config.llm?.max_output_tokens ?? 2000);
    if (budget > 0 && est > 0 && state.daily.spent_rmb + est > budget) {
      log(`[${topic.id}] 今日预算 ${budget} 元用完（已花 ${state.daily.spent_rmb.toFixed(4)}），跳过`);
      results.push({ topic: topic.id, skipped: 'budget' });
      continue;
    }

    const out = await buildTopic(topic, { config, baseDir, dir, force: true, log });
    st.cursor = items.map((it) => it.key);
    st.last_built_at = new Date().toISOString();
    st.last_items = out.items;
    st.last_error = null;
    state.daily.spent_rmb += est;
    results.push(out);
  }

  await saveState(statePath, state);
  return results;
}
