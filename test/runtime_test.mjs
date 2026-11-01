/**
 * Runtime tests: load the bundled example archive and make sure the block
 * that goes into a prompt actually contains the numbers a user would ask for.
 *
 *   node test/runtime_test.mjs
 */
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
process.env.MEMORY_ARCHIVE_JSON = join(HERE, '..', 'examples', 'data', 'archive.json');

const { archiveBlock, flatFacts, complexity, routeEvents, stats } = await import('../src/archive.mjs');

const pass = [];
const fail = [];
function check(name, ok, detail = '') {
  (ok ? pass : fail).push(name);
  console.log((ok ? '  ok   ' : '  FAIL ') + name + (!ok && detail ? `  [${detail}]` : ''));
}

const s = stats();
check('stats reads the example archive', s.events >= 10 && s.facts === 33, JSON.stringify(s));

const q1 = '备份阵列现在还剩多少容量？';
const b1 = archiveBlock(q1, { limit: 20, events: 3, perEvent: 6 });
check('capacity question returns a block', b1.length > 0, `${b1.length} chars`);
check('block carries the newest capacity number', b1.includes('15.1 TB'), b1.slice(0, 200));
check('block carries the earlier value too', b1.includes('13.4 TB'));

const q2 = '反向代理 502 那次最后怎么解决的？';
const b2 = archiveBlock(q2, { limit: 20, events: 3, perEvent: 6 });
check('incident question returns a block', b2.length > 0, `${b2.length} chars`);
check('block names the root cause', b2.includes('keepalive'), b2.slice(0, 200));

const q3 = '阵列第 5 块盘怎么样了';
const b3 = archiveBlock(q3, { limit: 20, events: 3, perEvent: 6 });
check('disk question returns a block', b3.length > 0);
check('block reports the replacement', b3.includes('26 小时') || b3.includes('换盘'), b3.slice(0, 200));

check('unrelated question stays quiet or short', archiveBlock('今天天气怎么样', { limit: 20, events: 2, perEvent: 4 }).length < b1.length);

check('complexity() grades a multi-part question', complexity('备份阵列先是多少，后来换了盘又是多少？').level >= 1);
check('routeEvents() returns scored events', routeEvents('证书过期', 3).every((it) => it.e && typeof it.s === 'number'));
check('flatFacts() returns lines', flatFacts('机柜功耗', 3).length > 0);

console.log();
console.log(`${pass.length} passed, ${fail.length} failed`);
if (fail.length) {
  console.log('failed: ' + fail.join(', '));
  process.exit(1);
}
