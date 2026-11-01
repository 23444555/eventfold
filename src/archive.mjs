/**
 * 事件档案（新项目）运行期实现 —— 零依赖单文件。
 *
 * 只用 node 内置：fs / path / url。不装包、不起服务、不调模型、不需要 Python。
 * 数据只有一个 archive.json（由 p0/export_archive.py 离线生成）。
 *
 * 分工：
 *   记忆库（Hindsight + dsh-memory 注入）= 每轮都跑，保下限：5 条事实 + 原话，点查准。
 *   档案（本文件）              = 不设闸门、不自动触发；由模型自己判断要不要调（MCP 工具 memory_archive）。
 *                                complexity() 只是给工具用的提示信号，不是拦截规则。
 *
 * 用法：
 *   import { archiveBlock, complexity, stats } from './archive.mjs'
 *   const block = archiveBlock('那波 502 后来怎么处理的', { limit: 14 })
 */

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
// 数据位置：显式环境变量 > 同目录 > ../data/。装进插件后数据在 <plugin>/data/archive.json。
const DATA_FILE = process.env.MEMORY_ARCHIVE_JSON
  ? String(process.env.MEMORY_ARCHIVE_JSON)
  : [join(HERE, 'archive.json'), join(HERE, '..', 'data', 'archive.json')].find((p) => existsSync(p)) ||
    join(HERE, 'archive.json');

let DATA = null;
let INDEX = null;

function load() {
  if (DATA) return DATA;
  DATA = JSON.parse(readFileSync(DATA_FILE, 'utf8'));
  return DATA;
}

/* ---------- 词面相似度：字符 bigram 覆盖率 ---------- */

function bigrams(s) {
  const t = String(s || '').toLowerCase().replace(/[^0-9a-z\u4e00-\u9fff]+/g, '');
  if (t.length < 2) return new Set(t ? [t] : []);
  const out = new Set();
  for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2));
  return out;
}

function cover(queryGrams, text) {
  if (!queryGrams.size) return 0;
  let hit = 0;
  const seen = new Set();
  for (const g of bigrams(text)) {
    if (queryGrams.has(g) && !seen.has(g)) { seen.add(g); hit++; }
  }
  return hit / queryGrams.size;
}

/** 两个 bigram 集合的 Jaccard（槽位值比对用） */
function jaccard(a, b) {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  return inter / (a.size + b.size - inter);
}

const STOP_WORDS = [
  '什么','怎么','为什么','怎么样','多少','哪些','哪个','哪里','是不是','有没有','能不能','要不要',
  '一共','总共','到底','究竟','后来','最后','之前','之后','当时','现在','目前','情况','东西','事情','问题',
  '我们','你们','他们','我的','你的','他的','她的','我','你','他','她','它','这个','那个','这次','那次',
  '一下','可以','应该','需要','知道','告诉','觉得','还是','就是','然后','如果','因为','所以','但是',
  '的','了','吗','呢','吧','啊','有','没','是','和','跟','还','就','也','都','很','太','个','次','回','在','说','过',
]

/** 去掉问句里的口水词，只留「有信息量的部分」；否则「我的猫叫什么」会靠「什么」匹配到一堆无关事件。 */
function queryOnly(s) {
  let t = String(s || '');
  for (const w of STOP_WORDS) t = t.split(w).join(' ');
  return t;
}

/* ---------- 索引：每个事件一份「路由文本」+ 词面 IDF ---------- */

function buildIndex() {
  if (INDEX) return INDEX;
  const { events } = load();
  const items = events.map((e) => {
    const slotText = e.slots.map((s) => `${s.n}：${s.v}`).join(' ');
    const factText = e.facts.slice(0, 30).map((f) => f.t).join(' ');
    const route = `${e.title} ${e.summary} ${slotText} ${factText}`;
    return { e, route, grams: bigrams(route) };
  });
  // 每个 bigram 出现在多少个事件里 → idf；稀有的词（502、显卡、死锁）权重天然高
  const df = new Map();
  for (const it of items) for (const g of it.grams) df.set(g, (df.get(g) || 0) + 1);
  const N = items.length;
  const idf = (g) => Math.log((N + 1) / ((df.get(g) || 0) + 1)) + 0.2;
  for (const it of items) {
    let total = 0;
    for (const g of it.grams) total += idf(g);
    it.weight = total;
  }
  // 型号/报错码这类 token 也有自己的 df：502 只出现在 1 个事件里，17 出现在二十几个里
  const tokenDf = new Map();
  for (const it of items) {
    const head = `${it.e.title} ${it.e.slots.map((x) => x.v).join(' ')}`.toLowerCase();
    for (const tk of new Set(head.match(/[a-z]+[0-9]*|[0-9]{2,}/g) || [])) {
      tokenDf.set(tk, (tokenDf.get(tk) || 0) + 1);
    }
  }
  INDEX = { items, idf, N, df, tokenDf };
  return INDEX;
}

/** IDF 加权覆盖：只算「库里真出现过」的问句 bigram；稀有词命中比常见词重要。 */
function coverIdf(queryGrams, item) {
  const { idf, N } = buildIndex();
  let want = 0;
  let got = 0;
  let rareWant = 0;
  let rareGot = 0;
  for (const g of queryGrams) {
    const d = dfOf(g);
    if (!d) continue; // 库里根本没有这个词，不能算进分母
    const w = idf(g);
    want += w;
    const hit = item.grams.has(g);
    if (hit) got += w;
    if (d <= Math.max(2, Math.round(N * 0.08))) {
      rareWant += w;
      if (hit) rareGot += w;
    }
  }
  if (!want) return 0;
  const all = got / want;
  const rare = rareWant ? rareGot / rareWant : all;
  // 标题/槽位命中额外加分：标题是人工可读的事件名，命中它比命中正文更可信
  const titleG = bigrams(`${item.e.title} ${item.e.slots.map((s) => s.v).join(' ')}`);
  let tg = 0;
  let tn = 0;
  for (const g of queryGrams) {
    if (!dfOf(g)) continue;
    tn++;
    if (titleG.has(g)) tg++;
  }
  const bonus = tn ? 0.25 * (tg / tn) : 0;
  return Math.min(1.2, 0.6 * all + 0.4 * rare + bonus);
}

function dfOf(g) {
  return buildIndex().df.get(g) || 0;
}

/* ---------- 复杂度闸门（纯规则，微秒级） ---------- */

const MULTI_HOP = /(先后|先.*再|之后|后来|最后|前前后后|一共|总共|加起来|合计|对比|相比|为什么|为啥|原因|经过|怎么回事|怎么搞的|多久才|断断续续|几次|几个阶段)/;
const SPAN = /(这阵子|那阵子|这段时间|那段时间|上个月|上周|月初|月底|整个|从头|全程|整个流程)/;
const ANAPHORA = /(那件事|这件事|这个事|那次|那批|那个问题|上面说的|刚才说的)/;
const ARITH = /(\d+(\.\d+)?\s*(天|小时|分钟|秒|块|元|万|台|人|次).*?(加|减|乘|除|倍|多|少|省|剩|一共|总共))/;

export function complexity(query) {
  const q = String(query || '');
  const reasons = [];
  let level = 0;
  if (MULTI_HOP.test(q)) { reasons.push('多跳/归因词'); level = Math.max(level, 1); }
  if (ANAPHORA.test(q)) { reasons.push('指代前情'); level = Math.max(level, 1); }
  if (SPAN.test(q)) { reasons.push('跨时间段'); level = Math.max(level, 1); }
  if (ARITH.test(q)) { reasons.push('算术'); level = Math.max(level, 1); }
  // 一次问两件事（两个问号 / 两个「吗」「呢」）—— 常见于多跳
  const marks = (q.match(/[？?]/g) || []).length;
  if (marks >= 2) { reasons.push('一句话问两件'); level = Math.max(level, 1); }
  // 特别长的问题往往是要铺开讲
  if (q.length >= 28) { reasons.push('问句长'); level = Math.max(level, 1); }
  // 记忆库给的结果太空（外部塞进来的信号）也升级：level2 = 档案优先
  return { level, reasons };
}

/** 记忆库结果很弱时，把闸门抬到 2（档案优先）。由调用方决定要不要用。 */
export function escalate(verdict, recall) {
  const out = { ...verdict };
  const n = Array.isArray(recall) ? recall.length : 0;
  const best = n && typeof recall[0]?.score === 'number' ? recall[0].score : null;
  if (n === 0) { out.level = 2; out.reasons = [...out.reasons, '记忆库空手']; }
  else if (best !== null && best < 0.35) { out.level = Math.max(out.level, 2); out.reasons = [...out.reasons, '记忆库最高分很低']; }
  return out;
}

/* ---------- 检索：路由事件 → 铺事实 ---------- */

function dateIn(q) {
  const m = q.match(/(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]/) || q.match(/(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

/** 路由前把日期从问句里剥掉：日期已经由 dateIn 单独当锚，
 *  留着会生出「26」「1-」这种 bigram，把每件带日期的陈年旧事都算成命中（10 月的发布史就是这样挤进前两名）。 */
function stripDates(s) {
  return String(s || '')
    .replace(/\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}[日号]?/g, ' ')
    .replace(/\d{1,2}\s*[-月/]\s*\d{1,2}\s*[日号]?/g, ' ');
}

export function routeEvents(query, n) {
  const qg = bigrams(queryOnly(stripDates(query)));
  const dm = dateIn(query);
  const tokens = String(query).toLowerCase().match(/[a-z]+[0-9]*|[0-9]{2,}/g) || [];
  const scored = buildIndex().items.map((it) => {
    let s = coverIdf(qg, it);
    let dateHit = false;
    let bodyHit = 0;
    let tokenHit = false;
    if (dm && (it.e.first || it.e.last)) {
      for (const day of [it.e.first, it.e.last]) {
        const [, mo, da] = (day || '').split('-').map(Number);
        if (mo === dm[0] && da === dm[1]) { s += 0.15; dateHit = true; break; }
      }
    }
    // 元信息对不上时退回正文：像「其他 / 管理规矩」这种大杂烩，标题没有信息量，
    // 但它的正文里就有 502、慢查询这些词——只看标题会把整件事漏掉。
    if (s < 0.3 && it.e.facts && it.e.facts.length) {
      let best = 0
      for (const f of it.e.facts) {
        const c = cover(qg, cleanFact(f.t))
        if (c > best) best = c
      }
      if (best > 0.18) {
        const add = 0.85 * best + 0.08
        if (add > s) { s = add; bodyHit = best }
      }
    }
    // 型号/报错码/专有名词（502、TTL、A100、2800）落在事件名或槽位里，几乎就是这一件事。
    // 匹配前先把日期剥掉：否则问句里的「11-26」会去命中每一条槽位里的「2026-11-25」，
    // 所有事件都白拿一份加分（实测把 10 月的发布事件抬到第 3、4 名，吃掉一半预算）。
    const head = `${it.e.title} ${it.e.slots.map((x) => x.v).join(' ')}`.toLowerCase();
    const headNoDate = head
      .replace(/\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}[日号]?/g, ' ')
      .replace(/\d{1,2}月\d{1,2}[日号]/g, ' ');
    const { tokenDf, N: total } = buildIndex();
    for (const tk of tokens) {
      if (tk.length < 2) continue;
      const numeric = /^\d+$/.test(tk);
      const hit = numeric
        ? new RegExp(`(?<!\\d)${tk}(?!\\d)`).test(headNoDate)
        : headNoDate.includes(tk);
      if (!hit) continue;
      const tdf = tokenDf.get(tk) || 1;
      s += 0.25 * Math.log((total + 1) / (tdf + 1)) + 0.1;
      tokenHit = true;
    }
    return { e: it.e, s, dateHit, bodyHit, tokenHit };
  });
  if (dm) {
    // 日期是最硬的锚：同月同日的事件就算词面不像，也先拉进来（跨年不管，
    // 这份档案只覆盖一个多月，年月歧义不存在）
    for (const it of scored) {
      const days = [it.e.first, it.e.last, ...(it.e.facts || []).map((x) => x.d)]
        .filter(Boolean)
        .map((d) => d.split('-'));
      if (days.some(([, mo, da]) => Number(mo) === dm[0] && Number(da) === dm[1])) {
        it.s = Math.max(it.s, 0.55);
        it.dateHit = true;
      }
    }
  }
  scored.sort((a, b) => b.s - a.s || a.e.id.localeCompare(b.e.id));
  if (process.env.ARCHIVE_DEBUG) {
    for (const x of scored.slice(0, 20)) {
      process.stderr.write(
        `[route-debug] ${x.s.toFixed(3)} ${x.e.id} date=${x.dateHit} body=${(x.bodyHit || 0).toFixed(2)} tok=${x.tokenHit}\n`,
      );
    }
  }
  // 问句里带了日期时，离那天太远的低分事件基本是「词面巧合」：问 11-26 的缓存，
  // 10-01 的发布史因为都有「内存」二字被排进前三，白吃一半预算。
  // 只砍「远 + 低分」的：日期锚到（0.55 起）或命中专名 token 的不受影响。
  if (dm) {
    const anchor = Date.UTC(2026, dm[0] - 1, dm[1]);
    const farDays = Number(process.env.ARCHIVE_FAR_DAYS || 30);
    const near = (e) => {
      let best = Infinity;
      for (const d of [e.first, e.last]) {
        const [y, mo, da] = String(d || '').split('-').map(Number);
        if (!y) continue;
        const gap = Math.abs(Date.UTC(y, mo - 1, da) - anchor) / 86400000;
        if (gap < best) best = gap;
      }
      return best;
    };
    for (const x of scored) if (!x.dateHit && !x.tokenHit) x.far = near(x.e) > farDays;
  }
  // 只靠「正文最像的那一条」补位进来的事件（大杂烩里 502 这类）要单独立规矩：
  // 它偶尔救命（CT1 的「其他」），但也会把整段无关的发布史拖进来占满预算。
  const min = Number(process.env.ARCHIVE_MIN_SCORE || 0.18);
  const bodyMin = Number(process.env.ARCHIVE_BODY_MIN || 0.34);
  const fillerMax = Math.max(0, Number(process.env.ARCHIVE_FILLER_MAX ?? 1));
  // 低分事件只许「同一条线的其它阶段」进来：问 11-26 的缓存，TTL 的 11-02/11-09 是这条线
  // 的前几段，有用；而「子智能体熔断」那种只靠「内存」两个字撞上的事，给不了答案却占一半预算。
  // 大杂烩（未分类：其他）没有主体身份，既不当锚也不靠主体混进来。
  const subjGate = String(process.env.ARCHIVE_SUBJ_GATE || 'on').toLowerCase() !== 'off';
  const gateMin = Number(process.env.ARCHIVE_SUBJ_GATE_MIN || 0.4);
  const subjOf = (e) => (e.subj && !/其他|未分类/.test(e.subj) ? e.subj : '');
  const anchorSubjs = new Set();
  const out = [];
  let filler = 0;
  for (const x of scored) {
    if (x.s < min) break;
    if (x.far) continue;
    const isFiller = !!(x.bodyHit && !x.dateHit && !x.tokenHit);
    if (isFiller) {
      if (x.s < bodyMin) continue;
      if (filler >= fillerMax) continue;
    } else if (
      subjGate &&
      !x.dateHit &&
      !x.tokenHit &&
      x.s < gateMin &&
      anchorSubjs.size &&
      !anchorSubjs.has(subjOf(x.e))
    ) {
      continue;
    }
    if (isFiller) filler++;
    out.push(x);
    const sub = subjOf(x.e);
    if (sub && x.s >= gateMin) anchorSubjs.add(sub);
    if (out.length >= n) break;
  }
  return out;
}

/** 同一件事在库里常有「原句版 + 改写版」，铺开时只留一条。 */
function cleanFact(t) {
  return String(t || '').replace(/\s*\|\s*When:.*$/i, '').trim();
}

/** 去重前先剥掉日期与空白：「张伟为下单失误道歉」和「张伟于2026年10月6日为下单失误道歉」是同一条。 */
function dedupeKey(t) {
  return cleanFact(t)
    .replace(/\d{4}[-年]\d{1,2}[-月]\d{1,2}[日号]?/g, '')
    .replace(/\d{1,2}月\d{1,2}[日号]/g, '')
    .replace(/[\s，。、；：]/g, '');
}

function dedupeFacts(list) {
  const kept = [];
  const grams = [];
  for (const entry of list) {
    const g = bigrams(dedupeKey(entry.f.t));
    let dup = false;
    for (let i = 0; i < kept.length; i++) {
      const kg = grams[i];
      let inter = 0;
      for (const x of g) if (kg.has(x)) inter++;
      const jac = inter / (g.size + kg.size - inter || 1);
      if (jac >= 0.45) { dup = true; break; }
    }
    if (!dup) { kept.push(entry); grams.push(g); }
  }
  return kept;
}

function renderEvent(e, query, cap, qg) {
  // 问句里点了日子（「11-26 内存里跑的还是多少秒」），那一天的事实必须先上桌：
  // 合并过的事件横跨好几天，纯按相似度挑会把关键那天挤掉。
  const want = dateIn(query);
  const onDay = (d) => {
    if (!want || !d) return false;
    const [, mo, da] = String(d).split('-').map(Number);
    return mo === want[0] && da === want[1];
  };
  const lines = [`### ${e.first}${e.last && e.last !== e.first ? '~' + e.last.slice(5) : ''} ${e.title}（${e.n} 条事实）`];
  // 习惯层：这类事的标准过程直接写进块里，模型照着走就不会漏步骤（不必自己去想该查什么）
  if (Array.isArray(e.proc) && e.proc.length) {
    lines.push('这类事的标准过程：' + e.proc.join(' → ') + '（问到哪一步就先答哪一步，缺的那步直接问，别猜）');
  }
  // 进化闭环产出的习惯（号主审过的工具流程）：告诉你通常按什么顺序去查
  if (Array.isArray(e.habit) && e.habit.length >= 2) {
    lines.push('以前遇到这类事，通常按这个顺序处理：' + e.habit.join(' → '));
  }
  if (e.slots.length) {
    // 槽位按「所属的那一段」分组：合并过的事件里，同一个名字可能每段各有一个值
  const spans = new Map();
  for (const sl of e.slots) {
    const k = sl.span || '';
    if (!spans.has(k)) spans.set(k, []);
    spans.get(k).push(sl);
  }
  if (spans.size <= 1) {
    lines.push('槽位：' + e.slots.slice(0, 8).map((s) => `${s.n}=${s.v}`).join('；'));
  } else {
    for (const [span, list] of [...spans.entries()].slice(0, 3)) {
      lines.push(`槽位（${span}）：` + list.slice(0, 6).map((s) => `${s.n}=${s.v}`).join('；'));
    }
  }
  }
  // 问句给了日子，就把那天的事实提到候选池最前面：合并/大杂烩事件有几百条，
  // 按词面相似度排，关键那天的三条根本挤不进前 72。
  const dayFirst = want ? e.facts.filter((f) => onDay(f.d)) : [];
  const daySet = new Set(dayFirst.map((f) => f.i));
  const scored = [...dayFirst.map((f) => ({ f, s: 1 })),
    ...e.facts.filter((f) => !daySet.has(f.i)).map((f) => ({ f, s: cover(qg, cleanFact(f.t)) }))]
    .sort((a, b) => b.s - a.s || a.f.d.localeCompare(b.f.d));
  // 只对「最有希望进块的候选」去重，避免大事件上 O(n²) 比较
  const pool = dedupeFacts(scored.slice(0, Math.max(cap * 6, 40)));
  // 切片选事实：不按相似度取前 N（那样同一件事的抄本会把别的面挤掉），
  // 而是贪心最大化「新信息」——每挑一条，就把它的词面加入已覆盖集合，
  // 后面的候选要靠「还没被覆盖的内容」和「换一天」来竞争。
  const picked = [];
  const covered = new Set();
  const days = new Set();
  // 那天的事实先整段收进来（它们才是问题的落点），剩下的名额再按 MMR 补
  for (const f of dayFirst) {
    if (picked.length >= cap) break;
    picked.push({ f, s: 1 });
    days.add(f.d);
    for (const g of bigrams(dedupeKey(f.t))) covered.add(g);
  }
  const pickedI = new Set(picked.map((x) => x.f.i));
  const rest = pool.filter((x) => !pickedI.has(x.f.i));
  while (picked.length < cap && rest.length) {
    let bestI = 0;
    let bestV = -1;
    for (let i = 0; i < rest.length; i++) {
      const { f: cand, s: rel } = rest[i];
      const gs = bigrams(dedupeKey(cand.t));
      let novel = 0;
      for (const g of gs) if (!covered.has(g)) novel++;
      const novelty = gs.size ? novel / gs.size : 0;
      const v = 0.6 * rel + 0.4 * novelty + (days.has(cand.d) ? 0 : DAYBONUS) + (onDay(cand.d) ? 0.6 : 0);
      if (v > bestV) { bestV = v; bestI = i; }
    }
    const chosen = rest.splice(bestI, 1)[0];
    const gs = bigrams(dedupeKey(chosen.f.t));
    for (const g of gs) covered.add(g);
    days.add(chosen.f.d);
    picked.push(chosen);
  }
  picked.sort((a, b) => a.f.d.localeCompare(b.f.d));
  for (const { f } of picked) lines.push(`- ${f.d} ${cleanFact(f.t)}`);
  return { txt: lines.join('\n'), used: picked.length, kept: picked.map((x) => x.f.t) };
}

/**
 * 组装档案块。返回 '' 表示这块没有可用内容（调用方就只发记忆库那块）。
 * limit：整块最多铺多少条事实（默认 14）；perEvent：单个事件最多几条（默认 6）。
 */
function renderAll(query, routed, limit, perEvent) {
  const qg = bigrams(queryOnly(query));
  __lastUsed = [];
  const chunks = [];
  let used = 0;
  for (const { e } of routed) {
    if (used >= limit) break;
    const cap = Math.max(2, Math.min(perEvent, limit - used));
    const out = renderEvent(e, query, cap, qg);
    if (!out.used) continue;
    used += out.used;
    __lastUsed.push(e.id);
    chunks.push(out.txt);
  }
  if (!chunks.length) return '';
  const head = '【事件档案】同一个问题如果跨了几天或几件事，下面按「事件」整块给出当时的全貌；' +
    '它只是索引，事实以【事实】块里的原文为准。';
  return [head, ...chunks].join('\n');
}

/**
 * 跨阶段的问题（「最后回到多少」「先后改了几次」）不能只给一个事件：
 * 答案常在同一个「线索」的相邻阶段里。这里按线索（subj）分组，
 * 组内按日期铺开，像讲故事一样把几个阶段排出来。
 */
const DAYBONUS = Number(process.env.ARCHIVE_DAYBONUS || 0.06);
const STORY_Q = /(最后|最终|先后|前前后后|一共|总共|几次|改回|回到|来回|整个过程|怎么走到|整条线|又改|又变)/;

export function routeStory(query, n) {
  const ranked = routeEvents(query, Math.max(n * 4, 24));
  if (!ranked.length) return [];
  const min = Number(process.env.ARCHIVE_MIN_SCORE || 0.18);
  const groups = new Map();
  for (const it of ranked) {
    const key = `${it.e.schema}::${it.e.subj || it.e.thread}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(it);
  }
  const ordered = [...groups.entries()]
    .map(([key, list]) => {
      const best = list[0].s;
      const breadth = Math.min(list.length, 4);
      return { key, list, score: best + 0.06 * (breadth - 1) };
    })
    .sort((a, b) => b.score - a.score);
  const out = [];
  for (const g of ordered) {
    const episodes = g.list
      .filter((it) => it.s >= Math.max(min * 0.8, g.list[0].s * 0.45))
      .sort((a, b) => (a.e.first || '').localeCompare(b.e.first || ''))
      .slice(0, 3);
    for (const it of episodes) {
      if (!out.some((x) => x.e.id === it.e.id)) out.push(it);
    }
    if (out.length >= n) break;
  }
  return out.slice(0, n);
}

/**
 * 结构化路由：问句里直接提到主体（TTL / 阈值 / 测试机 / 键盘）时，
 * 别指望词面相似度把事件排上来——按「这件事关于什么」直接取那条线索。
 * 人找回忆也是先想起「哦，是那台测试机」，再看它前后的阶段。
 */
export function routeSubjects(query, maxSubjects = 2, maxEpisodes = 3) {
  const qg = bigrams(queryOnly(query));
  if (!qg.size) return [];
  const bySubj = new Map();
  for (const it of buildIndex().items) {
    const key = `${it.e.schema}::${it.e.subj || it.e.thread}`;
    if (!bySubj.has(key)) bySubj.set(key, { subj: it.e.subj || it.e.thread, list: [] });
    bySubj.get(key).list.push(it.e);
  }
  const scored = [];
  for (const [, g] of bySubj) {
    const sg = bigrams(g.subj);
    if (!sg.size) continue;
    let hit = 0;
    for (const x of sg) if (qg.has(x)) hit++;
    const cover = hit / sg.size;
    // 主体很短（2~6 字），要求「几乎整句都出现在问句里」才算点到名
    if (cover < 0.6) continue;
    scored.push({ subj: g.subj, cover, eps: g.list.slice().sort((a, b) => (a.first || '').localeCompare(b.first || '')) });
  }
  scored.sort((a, b) => b.cover - a.cover || a.subj.localeCompare(b.subj));
  const out = [];
  for (const s of scored.slice(0, maxSubjects)) {
    for (const e of s.eps.slice(-maxEpisodes)) {
      if (!out.some((x) => x.e.id === e.id)) out.push({ e, s: 0.35 + 0.2 * s.cover });
    }
  }
  return out;
}

let __lastUsed = [];

/** 上一次 archiveBlock 实际铺进了哪些事件（命中统计用，不影响检索）。 */
export function lastUsedEvents() {
  return __lastUsed.slice();
}

export function archiveBlock(query, opts = {}) {
  let { limit = 40, events: maxEvents = 5, perEvent = 10, threads = 0 } = opts;
  // 多腿题（一问两件、归因、跨段）用常规预算必然只铺到一半：40/5/12 只到 12/15，
  // 32/5/8 更差（9/15）。所以按问句复杂度自己抬预算，简单题照旧省上下文。
  if (
    String(process.env.ARCHIVE_BOOST || 'on').toLowerCase() !== 'off' &&
    (complexity(query).level >= 1 || intentHit(query))
  ) {
    limit = Math.max(limit, Number(process.env.ARCHIVE_BOOST_LIMIT || 48));
    maxEvents = Math.max(maxEvents, Number(process.env.ARCHIVE_BOOST_EVENTS || 5));
    perEvent = Math.max(perEvent, Number(process.env.ARCHIVE_BOOST_PER || 14));
  }
  // 铺开的事件多了，整块名额也要跟着涨，否则后面补齐的那段照样被 limit 截掉。
  const growLimit = (routed) =>
    Math.max(limit, Math.min(Number(process.env.ARCHIVE_LIMIT_MAX || 52), routed.length * perEvent));
  const qg = bigrams(queryOnly(query));
  // 先按主体直取（结构化），剩下的位子再用相似度路由补
  const bySubject = routeSubjects(query, Number(process.env.ARCHIVE_SUBJ || 2), Number(process.env.ARCHIVE_SUBJ_EPS || 3));
  if (bySubject.length) {
    const ranked0 = routeEvents(query, Math.max(maxEvents * 3, 12));
    const merged = [...bySubject];
    for (const it of ranked0) {
      if (merged.length >= maxEvents + bySubject.length) break;
      if (!merged.some((x) => x.e.id === it.e.id)) merged.push(it);
    }
    const merged2 = bridgeRelated(expandNeighbors(expandSiblings(merged.slice(0, maxEvents + 1)), Number(process.env.ARCHIVE_NEIGH_MAX || 2)));
    const body = renderAll(query, merged2, growLimit(merged2), perEvent);
    if (body) return withIntent(query, body);
  }
  if (threads <= 0 && STORY_Q.test(String(query || ''))) {
    const story = routeStory(query, maxEvents);
    if (story.length > 1) {
      const body = renderAll(query, story, limit, perEvent);
      if (body) return withIntent(query, body);
    }
  }
  const ranked = routeEvents(query, Math.max(maxEvents * 3, 12));
  if (!ranked.length) return '';
  // 先在「线」这一层排名，再在线内轮流取事件：
  // 一条线（thread）才是一件事情的完整经过，单个事件只是它的一段；
  // 一个问题问「最后回到多少」时，答案往往在线里相邻的另一段。
  if (threads <= 0) {
    // 纯全局排名：老口径，用来对照
    const routed = bridgeRelated(expandNeighbors(expandSiblings(ranked.slice(0, maxEvents)), Number(process.env.ARCHIVE_NEIGH_MAX || 2)));
    return withIntent(query, renderAll(query, routed, growLimit(routed), perEvent));
  }
  const byThread = new Map();
  for (const it of ranked) {
    if (!byThread.has(it.e.thread)) byThread.set(it.e.thread, []);
    byThread.get(it.e.thread).push(it);
  }
  const lines = [...byThread.entries()]
    .sort((a, b) => (b[1][0].s || 0) - (a[1][0].s || 0))
    .slice(0, Math.max(1, threads));
  const routed = [];
  const seenId = new Set();
  for (let round = 0; routed.length < maxEvents; round++) {
    let added = false;
    for (const [, list] of lines) {
      const it = list[round];
      if (!it || seenId.has(it.e.id)) continue;
      seenId.add(it.e.id);
      routed.push(it);
      added = true;
      if (routed.length >= maxEvents) break;
    }
    if (!added) break;
  }
  return withIntent(query, renderAll(query, routed, limit, perEvent));
}

/**
 * 槽位桥（多腿题的第二条腿）：
 * 问「内存里跑的哪个阈值」时，答案的一半在另一段事件里——「缓存 TTL 改了没重启」，
 * 两段词面对不上（阈值 vs TTL），但槽位结构对得上（同一批人、同 schema、隔几天）。
 * 这里按槽位把它们捞回来，插在锚点事件后面，让两条腿在同一块里见。
 * 只认「同一槽位名 + 值近似」，不做同义词猜测。
 */
const BRIDGE_SLOTS = ['参数名', '变更人', '责任人', '物品', '现象', '根本原因'];
// 试过：开桥在 15 题上从 12 掉到 10（CT1/CC1/CC5 反而断腿），默认关，ARCHIVE_BRIDGE=on 才开。
const BRIDGE_ON = String(process.env.ARCHIVE_BRIDGE || '').toLowerCase() === 'on';

function bridgeRelated(routed, maxAdd = Number(process.env.ARCHIVE_BRIDGE_MAX || 2)) {
  if (!BRIDGE_ON || !maxAdd) return routed;
  const { events } = load();
  const have = new Set(routed.map((x) => x.e.id));
  const keys = [];
  for (const it of routed) {
    for (const s of it.e.slots || []) {
      if (!BRIDGE_SLOTS.includes(s.n)) continue;
      const v = String(s.v || '').trim();
      if (v.length < 2) continue;
      keys.push({ n: s.n, g: bigrams(v) });
    }
  }
  if (!keys.length) return routed;
  const win = Number(process.env.ARCHIVE_BRIDGE_DAYS || 10);
  const out = routed.slice();
  let added = 0;
  for (let idx = 0; idx < routed.length && added < maxAdd; idx++) {
    const anchor = routed[idx];
    const [ay, am, ad] = String(anchor.e.first || '').split('-').map(Number);
    if (!ay) continue;
    const base = Date.UTC(ay, am - 1, ad);
    const cands = [];
    for (const e of events) {
      if (have.has(e.id) || e.schema !== anchor.e.schema) continue;
      const [ey, em, ed] = String(e.first || '').split('-').map(Number);
      if (!ey) continue;
      const diff = Math.abs(Date.UTC(ey, em - 1, ed) - base) / 86400000;
      if (diff > win) continue;
      let hit = 0;
      for (const s of e.slots || []) {
        for (const k of keys) {
          if (s.n !== k.n) continue;
          if (jaccard(k.g, bigrams(String(s.v || ''))) >= 0.5) hit++;
        }
      }
      if (hit) cands.push({ e, s: 0.25 + 0.1 * hit - diff * 0.01, diff, hit });
    }
    cands.sort((a, b) => b.s - a.s || a.diff - b.diff);
    let at = out.findIndex((x) => x.e.id === anchor.e.id) + 1;
    for (const c of cands) {
      if (added >= maxAdd) break;
      if (have.has(c.e.id)) continue;
      have.add(c.e.id);
      out.splice(at++, 0, { e: c.e, s: c.s });
      added++;
    }
  }
  return out;
}

/**
 * 时间邻居：同族事件里，离已选事件 N 天内的那几段也算同一件事的前后脚。
 * 插在「锚点事件」后面而不是排在末尾——块长有上限，排末尾等于白拿。
 */
function expandNeighbors(routed, maxAdd = 2, days = Number(process.env.ARCHIVE_NEIGH_DAYS || 3)) {
  const { events } = load();
  const have = new Set(routed.map((x) => x.e.id));
  const out = routed.slice();
  let added = 0;
  for (let idx = 0; idx < routed.length && added < maxAdd; idx++) {
    const it = routed[idx];
    const [y, mo, da] = String(it.e.first || '').split('-').map(Number);
    if (!y) continue;
    const base = Date.UTC(y, mo - 1, da);
    const cands = [];
    for (const e of events) {
      if (have.has(e.id) || e.schema !== it.e.schema) continue;
      const [ey, em, ed] = String(e.first || '').split('-').map(Number);
      if (!ey) continue;
      const diff = Math.abs(Date.UTC(ey, em - 1, ed) - base) / 86400000;
      if (diff <= days) cands.push({ e, s: 0.3 - diff * 0.05, diff });
    }
    cands.sort((x, y2) => x.diff - y2.diff);
    for (const c of cands) {
      if (added >= maxAdd) break;
      if (have.has(c.e.id)) continue;
      have.add(c.e.id);
      out.push({ e: c.e, s: c.s });
      added++;
    }
  }
  return out;
}

/**
 * 同主体兄弟段：问句只有日期、没有主体名时（「11-26 发现内存里跑的还是多少秒」），
 * 命中的往往是「发现没生效」那一段，真正的数值在同主体更早的一段里。
 * 只要锚点事件带 subj，就把同 subj 的最近一段补在它后面。
 */
function expandSiblings(routed, maxAdd = Number(process.env.ARCHIVE_SIB_MAX ?? 1)) {
  if (!maxAdd || !routed.length) return routed;
  const { events } = load();
  const have = new Set(routed.map((x) => x.e.id));
  const out = routed.slice();
  let added = 0;
  for (let idx = 0; idx < out.length && added < maxAdd; idx++) {
    const anchor = out[idx];
    const subj = anchor.e.subj;
    if (!subj) continue;
    const base = Date.parse(anchor.e.first);
    if (!Number.isFinite(base)) continue;
    const cands = events
      .filter((e) => !have.has(e.id) && e.subj === subj)
      .map((e) => ({ e, diff: Math.abs(Date.parse(e.first) - base) / 86400000 }))
      .filter((c) => Number.isFinite(c.diff) && c.diff <= Number(process.env.ARCHIVE_SIB_DAYS || 21))
      .sort((a, b) => a.diff - b.diff);
    for (const c of cands) {
      if (added >= maxAdd) break;
      if (have.has(c.e.id)) continue;
      have.add(c.e.id);
      out.splice(idx + 1 + added, 0, { e: c.e, s: 0.45 - c.diff * 0.01 });
      added++;
    }
  }
  return out;
}

/**
 * 意图模板：有些问题的两条腿分属两个事件，词面根本对不上——
 * 问「阈值」答案在「缓存没重启」，问「说做了没做」答案散在好几件事里。
 * 这里不猜词，改按「说法的形状」去捞：先由问句认出是哪类说法，再用谓词过一遍事实。
 * 规则极少（两条），宁缺不滥；命中条数在 19/1067 这个量级。
 */
const INTENT_RULES = [
  {
    id: 'config-not-effective',
    // 「内存里跑的到底是什么」——真正要的是「改了但没生效」的那些段
    test: /(还在跑|仍在跑|还在用|没生效|未生效|不生效|还是旧|旧的|没重启|未重启|生效了吗|生效没)/,
    fact: (t) =>
      /(内存|缓存|配置|参数|阈值|网关)/.test(t) && /(未|没|仍)/.test(t) && /(重启|生效|加载|运行)/.test(t),
  },
  {
    id: 'promise-broken',
    // 「说做了、实际没做」——承诺与落空
    test: /(说.*(没|未).*做|汇报|承诺|答应|嘴上|说了.*没做|实际.*没|说了没做)/,
    fact: (t) =>
      (/(说|答应|承诺|汇报|承认|表示|指出)/.test(t) &&
        /(没|未|忘|漏|误以为|并未)/.test(t) &&
        /(做|删|改|重启|加|落实|完成|处理|修)/.test(t)) ||
      // 只做了一半：只删了测试环境、忘了生产环境——这类答案里根本没有「说」字
      (/(只|仅)/.test(t) &&
        /(删|改|做|处理|修|重启)/.test(t) &&
        /(忘|漏|遗漏|没|未|没删|遗漏)/.test(t)),
  },
];

/** 只判断问句像不像这两类说法（不扫事实），给「抬预算」用：短问句也可能要铺两段。 */
function intentHit(query) {
  const q = String(query || '');
  return INTENT_RULES.some((r) => r.test.test(q));
}

function intentFacts(query, body, cap = Number(process.env.ARCHIVE_INTENT_MAX || 10)) {
  const q = String(query || '');
  const rules = INTENT_RULES.filter((r) => r.test.test(q));
  if (!rules.length) return [];
  const out = [];
  const seen = new Set();
  for (const e of load().events) {
    for (const f of e.facts) {
      const t = cleanFact(f.t);
      if (!t || seen.has(t)) continue;
      if (body.includes(t.slice(0, 24))) continue;
      if (!rules.some((r) => r.fact(t))) continue;
      seen.add(t);
      // 信号越全越可信：说了、没做、只做一半，命中几组算几分
      let sig = 0;
      if (/(说|答应|承诺|汇报|承认|表示|指出)/.test(t)) sig += 2;
      if (/(没|未|忘|漏|并未|误以为)/.test(t)) sig += 1;
      if (/(实际|结果|最后|都|也是|一样)/.test(t)) sig += 1;
      if (/(只|仅)/.test(t)) sig += 1;
      out.push({ f, sig });
    }
  }
  out.sort((a, b) => b.sig - a.sig || String(b.f.d).localeCompare(String(a.f.d)));
  // 同一句话在档案里常存两份（带日期/不带日期），去重后名额才够分给真正的两条腿
  const kept = [];
  for (const x of out) {
    const g = bigrams(cleanFact(x.f.t));
    if (kept.some((k) => jaccard(k.g, g) >= 0.6)) continue;
    kept.push({ ...x, g });
    if (kept.length >= cap) break;
  }
  return kept.map((x) => x.f);
}

/** 把旁证挂在事件块后面；没有命中就原样返回，不占一个字。 */
function withIntent(query, body) {
  if (!body) return body;
  if (String(process.env.ARCHIVE_INTENT || 'on').toLowerCase() === 'off') return body;
  const picked = intentFacts(query, body);
  if (!picked.length) return body;
  const lines = picked.map((f) => `- ${f.d} ${cleanFact(f.t)}`);
  return (
    body +
    '\n【同类事的旁证】下面几条不在上面的事件里，但说的是同一类事（按说法形状匹配到的，不是猜的）：\n' +
    lines.join('\n')
  );
}

export function flatFacts(query, k = 8) {
  const qg = bigrams(query);
  const all = [];
  for (const e of load().events) for (const f of e.facts) all.push(f);
  const scored = all.map((f) => ({ f, s: cover(qg, cleanFact(f.t)) }));
  scored.sort((a, b) => b.s - a.s || a.f.d.localeCompare(b.f.d));
  return dedupeFacts(scored.filter((x) => x.s > 0)).slice(0, k).map((x) => x.f);
}

export function stats() {
  const { events } = load();
  return {
    events: events.length,
    facts: events.reduce((a, e) => a + e.facts.length, 0),
    slots: events.reduce((a, e) => a + e.slots.length, 0),
    withSlots: events.filter((e) => e.slots.length).length,
  };
}
