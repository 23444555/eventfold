# dossier

把散落的对话事件整理成**按主题的档案**，需要时只注入命中那一篇（默认 600 字）。

RAG 是「检索原文片段」，档案是「提前把一件事的来龙去脉写成人话」。前者省 token 但不连贯，后者连贯但会旧——所以档案带生成时间、素材游标和重建命令，可追溯、可重来。

```
事件 / 事实  ──►  build（LLM + 三道护栏） ──►  hardware.md
                                             memory-system.md
                                                  │
问句 ──► matchTopics（关键词，最多一篇） ──► inject（≤600 字）──► 提示词
```

## 为什么不是「把记忆全塞进去」

用仓库自带的 `bench/questions.json`（9 道题）跑出来：

| 注入方式 | 答对 | 材料字数 |
|---|---|---|
| 不注入 | 1/9 | 0 |
| 只给命中那篇 | 7/7 | ~900 |
| 三篇全给 | 9/9 | ~1,600 |
| 旧版本档案（`.bak`） | 7/7 | ~900 |

**多给不涨分，只涨 3.2 倍材料。** 所以注入端只挑一篇、600 字封顶。

## 三个失败模式（这是本项目真正的说明书）

在真实群里跑出来的，不是假想：

1. **冒名**：硬件篇 45% 的要点、系统篇 44% 的要点，主语其实是群友——「李总批了预算」「老周把 TTL 调到 3600」。数字都能溯源，但人错了。
   → 护栏：`owner_only` 只收以「用户/我」开头的事件；生成后 `stripForeignSubjects()` 把「有他人姓名且同句没有号主指代」的行删掉。
2. **投毒照抄**：把档案里的 `4070 Ti Super` 改成 `4090` 再问，5 道题里 **2 道照抄错值**。
   → 结论：档案不是真相，只是摘录。注入时保留 `built` 时间戳，事实必须能回到原始素材。护栏只能保证「数字在素材里出现过」，保证不了「素材是对的」。
3. **新鲜度测不出来**：拿 10 月 31 日的旧档案跑同一批题，还是 12/12——因为素材本来就跨到 12 月。
   → 结论：靠「感觉旧了」重建不可靠，要靠 cursor：素材有新 key 才重建。

## 护栏住在隔壁

三个护栏（结构 / 数字可溯源 / 主体过滤）已经抽成独立的零依赖包 [`fold-guard`](../fold-guard)，
事件摘要、周报这类"LLM 写的档案"可以共用同一套规则。本包的 `src/guard.mjs` 只保留自己的观点：
哪些小节必须有、哪些名字算"别人"。

## 快速开始

```bash
npm i -g dossier
mkdir my-bot && cd my-bot
dossier init                 # 生成 dossier.config.json + 示例素材
export DEEPSEEK_API_KEY=...  # 或用任何 OpenAI 兼容端点
dossier build --all          # 生成 hardware.md / memory-system.md
dossier inject "我显卡什么型号"
```

`inject` 输出（≤600 字，直接拼进提示词）：

```
# 硬件与工位
> 一句话：4070 Ti Super 机况告急，另租 A100 云服务器。
## 当前状态
- 现有显卡为 2026年9月更换的 4070 Ti Super。
- 机箱因静电火花快要报废，D盘也快要冒烟。
...
```

## 配置

```json
{
  "llm": { "model": "deepseek-chat", "base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY" },
  "budget": { "daily_rmb": 0.2, "min_new_items": 1 },
  "topics": [{
    "id": "hardware",
    "title": "硬件与工位",
    "file": "hardware.md",
    "source": "archive",
    "archive": "events.sample.json",
    "owner_only": true,
    "filter": { "schemas": ["purchase-hardware"], "subjects": ["显卡", "机箱"] },
    "window_days": 365,
    "max_items": 15,
    "section_hint": "现在有什么 / 出过什么问题 / 想买什么",
    "keywords": ["显卡", "机箱", "A100", "4090"]
  }]
}
```

- `source`：`archive`（事件流）或 `facts`（已抽取的事实）。
- `owner_only`：只保留以「用户/你/主人/本人/我」开头的事件。群聊语料建议打开。
- `keywords`：注入时用来挑主题，命中数最多的那篇胜出；一个都不中就不注入。
- `budget.daily_rmb`：按 `llm.pricing` 估算，超了当天不再重建。

## 命令

| 命令 | 作用 |
|---|---|
| `dossier init` | 生成配置与示例素材 |
| `dossier build --all` / `--topic hardware` | 全量重建（`--dry` 只打印 prompt） |
| `dossier tick` | 增量：有新素材才重建，更新 cursor |
| `dossier list` | 看每个主题的上次构建时间与素材数 |
| `dossier inject "…"` | 按关键词挑一篇，输出 ≤600 字（`--chars` 可调） |

## 开发

```bash
npm test          # 护栏与注入的单元测试（node --test，无依赖）
npm run bench     # 四变体消融，需要 API key
```

## 已知边界

- 只处理「按主题」的档案；跨主题的问题（「我换显卡之后记忆库怎么样了」）需要多篇，当前只给一篇。
- 关键词匹配是子串匹配，不做同义词/分词。中文场景够用，语义相近的换法会漏。
- 同一主题的档案是整篇重写，不是 diff 合并；素材很多时可能丢掉旧要点（靠 `.bak` 兜底）。
- 不做事实校验，只做「数字是否出现在素材里」和「主语是不是本人」。素材本身错了，档案会跟着错。

## License

MIT
