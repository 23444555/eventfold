// Source adapter: an archive file of conversation events.
//
// Event shape (see examples/events.sample.json):
//   { id, date: "YYYY-MM-DD", schema, subject, summary, source? }
//
// `owner_only` keeps event summaries that start with the owner word. In group
// chats most events belong to other people; without this filter a dossier about
// "my hardware" ends up describing a colleague's purchase.

const OWNER_START = /^(用户|你|主人|本人|我)/;

export function withinWindow(date, days, now = new Date()) {
  if (!date) return true;
  const t = Date.parse(date);
  if (Number.isNaN(t)) return true;
  return (now.getTime() - t) / 86400000 <= days;
}

export function matchFilter(event, filter = {}) {
  const schemas = filter.schemas || [];
  const subjects = filter.subjects || [];
  if (schemas.length && !schemas.includes(event.schema)) return false;
  if (subjects.length) {
    const hay = `${event.subject || ''} ${event.summary || ''}`;
    if (!subjects.some((s) => hay.includes(s))) return false;
  }
  return true;
}

export async function load(topic, { readJson }) {
  const archive = await readJson(topic.archive || 'examples/events.sample.json');
  const events = Array.isArray(archive) ? archive : archive.events || [];
  const windowDays = topic.window_days ?? 180;

  return events
    .filter((e) => matchFilter(e, topic.filter))
    .filter((e) => !topic.owner_only || OWNER_START.test(String(e.summary || '').trim()))
    .filter((e) => withinWindow(e.date, windowDays))
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
    .map((e) => ({
      key: `ev:${e.id}`,
      date: e.date,
      text: String(e.summary || '').trim(),
    }));
}
