// The dossier's opinion about what a guard should check. The checks themselves
// live in `fold-guard` so the same rules can be shared with other folds
// (episode summaries, digests) instead of being re-implemented per package.

import { createGuard, DEFAULT_EXEMPT, DEFAULT_OWNER_WORDS } from 'fold-guard';

export const SECTIONS = ['当前状态', '时间线', '待跟进', '已变化'];

// People who appear in group chats but are not the owner. Extend for your data.
// Placeholder names on purpose: this file ships publicly.
export const OTHER_PEOPLE = [
  '张三', '李四', '王五', '赵六', '孙七', '周八', '吴九', '郑十',
  '张伟', '李娜', '王强', '刘洋',
];

const guard = createGuard({ sections: SECTIONS, people: OTHER_PEOPLE });

export { DEFAULT_EXEMPT, DEFAULT_OWNER_WORDS };
export const checkStructure = (...a) => guard.checkStructure(...a);
export const untraceable = (...a) => guard.untraceable(...a);
export const stripGuardLines = (...a) => guard.stripGuardLines(...a);
export const foreignSubjectLines = (...a) => guard.foreignSubjectLines(...a);
export const stripForeignSubjects = (...a) => guard.stripForeignSubjects(...a);
export const runGuards = (...a) => guard.run(...a);
