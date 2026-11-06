// The dossier's opinion about what a guard should check. The checks themselves
// live in `fold-guard` so the same rules can be shared with other folds
// (episode summaries, digests) instead of being re-implemented per package.

import { createGuard, DEFAULT_EXEMPT, DEFAULT_OWNER_WORDS } from 'fold-guard';

export const SECTIONS = ['当前状态', '时间线', '待跟进', '已变化'];

// People who appear in group chats but are not the owner. Extend for your data.
export const OTHER_PEOPLE = [
  '李总', '张伟', '李四', '赵敏', '周杰', '王工', '老周', '张三', '王五',
  '郑凯', '张伟明', '小鹿', '阿茶', '唐果', '老吴', '小胖', '小陈', '老李',
];

const guard = createGuard({ sections: SECTIONS, people: OTHER_PEOPLE });

export { DEFAULT_EXEMPT, DEFAULT_OWNER_WORDS };
export const checkStructure = (...a) => guard.checkStructure(...a);
export const untraceable = (...a) => guard.untraceable(...a);
export const stripGuardLines = (...a) => guard.stripGuardLines(...a);
export const foreignSubjectLines = (...a) => guard.foreignSubjectLines(...a);
export const stripForeignSubjects = (...a) => guard.stripForeignSubjects(...a);
export const runGuards = (...a) => guard.run(...a);
