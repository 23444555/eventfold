// Injection: pick the one topic whose keywords appear in the question, then emit a
// short excerpt. Measured on the source system: injecting all three topics scored
// exactly the same as injecting the matching one, at 3.2x the characters. So: one.
//
// The excerpt is deliberately small — "当前状态" 5 bullets + "待跟进" 3 bullets,
// capped (default 600 chars). A dossier is a header, not a document.

export function matchTopics(query, topics) {
  const q = String(query || '');
  const scored = topics.map((t) => {
    const kws = t.keywords || [];
    const hits = kws.filter((k) => q.includes(k)).length;
    return { topic: t, hits };
  });
  scored.sort((a, b) => b.hits - a.hits);
  return scored.filter((s) => s.hits > 0);
}

function sectionBullets(doc, name, limit) {
  const lines = String(doc).split('\n');
  const start = lines.findIndex((l) => l.trim() === `## ${name}`);
  if (start < 0) return [];
  const out = [];
  for (let i = start + 1; i < lines.length; i++) {
    const line = lines[i];
    if (/^##\s/.test(line)) break;
    if (/^\s*[-*]\s+/.test(line)) out.push(line.trimEnd());
    if (out.length >= limit) break;
  }
  return out;
}

function oneLiner(doc) {
  const m = String(doc).match(/^\s*>\s*一句话[:：]\s*(.+)$/m);
  return m ? m[1].trim() : '';
}

export function renderExcerpt(doc, maxChars = 600) {
  const title = (String(doc).match(/^\s*#\s+(.+)$/m) || [, ''])[1].trim();
  const head = title ? `# ${title}` : '';
  const line = oneLiner(doc);
  const parts = [head, line ? `> ${line}` : ''];
  const cur = sectionBullets(doc, '当前状态', 5);
  if (cur.length) parts.push('## 当前状态', ...cur);
  const todo = sectionBullets(doc, '待跟进', 3);
  if (todo.length) parts.push('## 待跟进', ...todo);
  let text = parts.filter(Boolean).join('\n');
  if (text.length > maxChars) text = `${text.slice(0, maxChars - 1)}…`;
  return text;
}

export function inject({ query, topics, dir, readFile, maxChars = 600 }) {
  const ranked = matchTopics(query, topics);
  if (!ranked.length) return { matched: null, text: '' };
  const topic = ranked[0].topic;
  const doc = readFile(topic.file);
  return { matched: topic.id, hits: ranked[0].hits, text: renderExcerpt(doc, maxChars) };
}
