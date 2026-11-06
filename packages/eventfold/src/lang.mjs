/**
 * Language packs — the only place in the runtime that assumes a natural language.
 *
 * `zh` is the default, the maintained one: its stop-word list was tuned on real
 * conversations and its date grammar covers `2026-11-05`, `11月5日`, `11-5`.
 *
 * `en` is a beta pack: it routes and it reads dates, which is what decides whether
 * the right episode comes back. The rendered block scaffolding is still Chinese
 * (see `docs/LANGUAGE.md`), and the subject guard needs a name list either way.
 *
 * A pack is a plain object:
 *   name          string
 *   stopWords     string[]
 *   queryOnly(s)  string            query with filler removed
 *   grams(s)      Set<string>       what "the same words" means
 *   parseDate(q)  [month, day] | null
 *   stripDates(s) string            dates removed, so they anchor instead of scoring
 *   dayParts(d)   [month, day]      from a stored `YYYY-MM-DD`
 */

export const ZH_STOP_WORDS = [
  '什么','怎么','为什么','怎么样','多少','哪些','哪个','哪里','是不是','有没有','能不能','要不要',
  '一共','总共','到底','究竟','后来','最后','之前','之后','当时','现在','目前','情况','东西','事情','问题',
  '我们','你们','他们','我的','你的','他的','她的','我','你','他','她','它','这个','那个','这次','那次',
  '一下','可以','应该','需要','知道','告诉','觉得','还是','就是','然后','如果','因为','所以','但是',
  '的','了','吗','呢','吧','啊','有','没','是','和','跟','还','就','也','都','很','太','个','次','回','在','说','过',
];

export const EN_STOP_WORDS = [
  'what','when','which','who','whom','whose','why','how','did','does','do','done','the','a','an','of','to',
  'in','on','at','for','and','or','but','if','then','that','this','these','those','it','its','is','was','were',
  'be','been','being','have','has','had','my','i','me','we','us','our','you','your','they','them','their','he',
  'she','him','her','about','any','some','there','here','with','from','by','as','not','no','so','just','also',
  'still','get','got','up','out','again','than','too','very','can','could','would','should','will','shall','may',
  'might','must','all','more','most','other','into','over','after','before','during','between','because','while',
];

const MONTHS = {
  january: 1, jan: 1, february: 2, feb: 2, march: 3, mar: 3, april: 4, apr: 4, may: 5, june: 6, jun: 6,
  july: 7, jul: 7, august: 8, aug: 8, september: 9, sep: 9, sept: 9, october: 10, oct: 10,
  november: 11, nov: 11, december: 12, dec: 12,
};

const EN_STOP = new Set(EN_STOP_WORDS);

const ZH = {
  name: 'zh',
  stopWords: ZH_STOP_WORDS,

  queryOnly(s) {
    let t = String(s || '');
    for (const w of ZH_STOP_WORDS) t = t.split(w).join(' ');
    return t;
  },

  grams(s) {
    const t = String(s || '').toLowerCase().replace(/[^0-9a-z\u4e00-\u9fff]+/g, '');
    if (t.length < 2) return new Set(t ? [t] : []);
    const out = new Set();
    for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2));
    return out;
  },

  parseDate(q) {
    const s = String(q || '');
    const m = s.match(/(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]/) || s.match(/(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)/);
    return m ? [Number(m[1]), Number(m[2])] : null;
  },

  stripDates(s) {
    return String(s || '')
      .replace(/\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}[日号]?/g, ' ')
      .replace(/\d{1,2}\s*[-月/]\s*\d{1,2}\s*[日号]?/g, ' ');
  },

  dayParts(d) {
    const [, mo, da] = String(d || '').split('-').map(Number);
    return [mo, da];
  },
};

const EN = {
  name: 'en',
  stopWords: EN_STOP_WORDS,

  queryOnly(s) {
    const toks = String(s || '').toLowerCase().match(/[a-z][a-z0-9']*|\d{1,}/g) || [];
    return toks.filter((t) => !EN_STOP.has(t)).join(' ');
  },

  /**
   * Words and adjacent word pairs. Pairs are what make "cache latency" beat a
   * stray "cache"; single words keep recall from collapsing when the question
   * paraphrases. Both go through the same IDF, so common pairs fade out.
   */
  grams(s) {
    const toks = String(s || '').toLowerCase().match(/[a-z][a-z0-9']*|\d{1,}/g) || [];
    const out = new Set();
    for (const t of toks) if (t.length >= 2 && !EN_STOP.has(t)) out.add(t);
    for (let i = 0; i < toks.length - 1; i++) {
      const a = toks[i];
      const b = toks[i + 1];
      if (EN_STOP.has(a) || EN_STOP.has(b)) continue;
      out.add(`${a} ${b}`);
    }
    return out;
  },

  parseDate(q) {
    const s = String(q || '');
    let m = s.match(/\b(\d{4})-(\d{1,2})-(\d{1,2})\b/);
    if (m) return [Number(m[2]), Number(m[3])];
    m = s.match(/\b(\d{1,2})\/(\d{1,2})\b/);
    if (m) return [Number(m[1]), Number(m[2])];
    m = s.match(/\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\b/);
    if (m) {
      const mo = MONTHS[m[1].toLowerCase()];
      if (mo) return [mo, Number(m[2])];
    }
    m = s.match(/\b(\d{1,2})\s+([A-Za-z]{3,9})\b/);
    if (m) {
      const mo = MONTHS[m[2].toLowerCase()];
      if (mo) return [mo, Number(m[1])];
    }
    return null;
  },

  stripDates(s) {
    const monthWord = (w) => MONTHS[String(w || '').toLowerCase().replace(/\.$/, '')];
    return String(s || '')
      .replace(/\b\d{4}-\d{1,2}-\d{1,2}\b/g, ' ')
      .replace(/\b\d{1,2}\/\d{1,2}\b/g, ' ')
      .replace(/\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\b/g, (m, mon) => (monthWord(mon) ? ' ' : m))
      .replace(/\b(\d{1,2})\s+([A-Za-z]{3,9})\b/g, (m, _day, mon) => (monthWord(mon) ? ' ' : m));
  },

  dayParts(d) {
    const [, mo, da] = String(d || '').split('-').map(Number);
    return [mo, da];
  },
};

export const LANGS = { zh: ZH, en: EN };

export function getLang(name) {
  return String(name || '').toLowerCase().startsWith('en') ? EN : ZH;
}

/** Selected by `MEMORY_ARCHIVE_LANG` (default `zh`), read per call so tests can switch. */
export function activeLang() {
  return getLang(process.env.MEMORY_ARCHIVE_LANG || 'zh');
}
