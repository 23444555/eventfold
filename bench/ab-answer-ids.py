"""给材料里的每条事实一个稳定编号（[F1234]），让模型答完标出「依据 Fx」。

假设：长材料 + 人名对齐不上，是模型不肯答（沉默 35/172）与答错的一部分原因；让它可以
「指认」一条具体的事实，可能降低犹豫、也让失败可归因。

对照是同一个材料、同一个问题，只是行首多了一个 id —— 基线用 ab-answer-v4.json 里
同名臂（`4800/vec`、`4800/sib25`），不重跑。

指标：
  - 答对率（另用 ab-rejudge-llm.py 复判，与基线同口径）
  - 沉默率
  - 引用命中：引用的 id 里有没有金标事实（`cite_hit`）
  - 引用纯净：引用的 id 全都属于金标（`cite_pure`）

用法：
  python bench/ab-answer-ids.py --gen bench/ab-gen-v4.json --blocks bench/ab-cap-blocks-v4.json \\
      --out bench/ab-answer-ids.json --arms 4800/vec,4800/sib25 --conc 12
"""
import argparse
import json
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))
ENV = r"E:\dshxz\hindsight\.env"

SYS_STRICT = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
              "材料里没有这条信息就只回「不知道」。"
              "回答完之后另起一行，写你用到的编号：依据 F1234,F5678（最多 3 个，必须是材料里出现过的）。")
SYS_LOOSE = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字；"
             "材料里没有这条信息就只回「不知道」。"
             "最后另起一行，只写你用到的编号，用逗号分开，最多 5 个，例如 F1234,F5678；"
             "一条都没用到就写一个「无」。")
SYS_NONE = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
            "材料里没有这条信息就只回「不知道」。")
SYS_FREE = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
            "材料里没有这条信息就只回「不知道」。"
            "回答之后请说明你是依据材料里哪几条得出的——怎么写都行，"
            "可以是一句话、可以用括号、可以夹在回答里，但不要套固定格式、不要只写编号清单。")
SYS_SOFT = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
            "材料里没有这条信息就只回「不知道」。"
            "如果你觉得自然，可以顺带提一句你依据的是材料里哪几条；不提也可以，怎么写都行。")
# 许可式（不是八股）：允许推算/换算，允许措辞不对应，只有完全没相关句子才弃答
SYS_DERIVE = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
              "材料里的句子可能没有直接写出答案，但能据此算出来、比出来、或看出结论时，就作答。"
              "只有当材料里完全没有相关句子时才回「不知道」。")
SYS_BESTGUESS = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
                 "只要材料里有和问题相关的句子，就给出最可能的答案——措辞不同、需要换算、需要取某个数字，"
                 "都照答；只有当材料里完全没有相关句子时才回「不知道」。")
SYS = SYS_STRICT

CITE_RE = re.compile(r"(?:依据|来源|出处)\s*[:：]?\s*((?:F\d+[\s,，、]*)+)", re.I)
BARE_CITE_RE = re.compile(r"^[\s\-—:：]*((?:F\d+[\s,，、]*)+)$", re.I)
ABSTAIN = re.compile(r"不知道|无法确定|没有相关|材料中没有|没提到|信息不足")


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, user, timeout=90):
    body = {"model": model,
            "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
            "max_tokens": 120, "temperature": 0, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            u = d.get("usage") or {}
            return (d["choices"][0]["message"].get("content") or "").strip(), u
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return "", {}


def norm(s):
    return re.sub(r"\s+", "", str(s or "").replace("| When:", "").strip())


def split_answer(txt):
    """把「答案 + 引用」拆开；同时认「依据 F1,F2」与「光秃秃一行 F1,F2」两种收口。"""
    txt = (txt or "").strip()
    cites = []
    ans = txt
    m = CITE_RE.search(txt)
    if m:
        cites = [int(x) for x in re.findall(r"F(\d+)", m.group(1), re.I)]
        ans = txt[: m.start()].strip()
    else:
        lines = [l for l in txt.split("\n") if l.strip()]
        if lines:
            m2 = BARE_CITE_RE.match(lines[-1])
            if m2:
                cites = [int(x) for x in re.findall(r"F(\d+)", m2.group(1), re.I)]
                ans = "\n".join(lines[:-1]).strip()
    ans = ans.split("\n")[0].strip()
    ans = re.sub(r"^[\s\"'「『]|[\s\"'」』]$", "", ans)
    return ans, cites[:5]


def strip_cites(text, gold=""):
    """把「依据 F1、F2」这类引用从句里剥掉，但保留含金标的那句（防止把答案一起删了）。"""
    t = (text or "").strip()
    g = re.sub(r"\s+", "", str(gold or ""))
    t = re.sub(r"[（(][^）)\n]*F\d+[^）)\n]*[）)]", "", t, flags=re.I)
    parts = re.split(r"(?<=[。；\n])", t)
    keep = []
    for s in parts:
        if not re.search(r"F\d+", s, re.I):
            keep.append(s)
            continue
        if g and len(g) >= 2 and g in re.sub(r"\s+", "", s):
            keep.append(re.sub(r"F\d+", "", s, flags=re.I))
    t = "".join(keep)
    t = re.sub(r"(?:依据|根据|来自|出处|参考|见)\s*[^。；\n]{0,40}", "", t)
    t = re.sub(r"F\d+", "", t, flags=re.I)
    t = re.sub(r"[，,、]\s*(?=[，,、。；]|$)", "", t)
    return re.sub(r"\s+", " ", t).strip(" -—:：,，、")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks-v4.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-answer-ids.json"))
    ap.add_argument("--arms", default="4800/vec,4800/sib25")
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--close", default="strict", choices=["strict", "loose", "none", "free", "soft",
                                                         "derive", "bestguess"],
                    help="收口方式：strict=「依据 F1,F2」最多 3 条；loose=末行只写编号；free=强制说依据但自由措辞；"
                         "soft=可提可不提；none=不提引用")
    args = ap.parse_args()

    global SYS
    SYS = {"strict": SYS_STRICT, "loose": SYS_LOOSE, "none": SYS_NONE,
           "free": SYS_FREE, "soft": SYS_SOFT,
           "derive": SYS_DERIVE, "bestguess": SYS_BESTGUESS}[args.close]
    key = load_key()
    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {f["i"]: f["t"] for f in gen["corpus"]}

    # 文本 → id（同文本多条时，优先挑金标里的那条）
    by_text = defaultdict(list)
    for f in gen["corpus"]:
        by_text[norm(f["t"])].append(f["i"])

    def fid_of(line, legs_flat):
        cand = by_text.get(norm(line), [])
        if not cand:
            return None
        for c in cand:
            if c in legs_flat:
                return c
        return min(cand)

    jobs = []
    for arm in args.arms.split(","):
        cap, name = arm.split("/")
        for iid, lines in blocks[cap][name].items():
            x = inst[iid]
            legs_flat = {f for leg in x["legs"] for f in leg}
            labelled = []
            for line in lines:
                fid = fid_of(line, legs_flat)
                labelled.append(f"[F{fid}] {line}" if fid else line)
            q = x["q"]
            user = "【材料】\n" + "\n".join(f"- {l}" for l in labelled) + f"\n\n【问题】{q}\n\n【回答】"
            jobs.append((arm, iid, x, q, user, legs_flat, len(lines)))
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"{len(jobs)} 次请求｜臂 {args.arms}｜模型 {args.model}", flush=True)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        out = list(ex.map(lambda j: call(args.base, key, args.model, j[4]), jobs))
    tin = sum(u.get("prompt_tokens", 0) for _t, u in out)
    tout = sum(u.get("completion_tokens", 0) for _t, u in out)
    print(f"完成 {len(out)}｜{time.time()-t0:.0f}s｜token 入 {tin} / 出 {tout}", flush=True)

    rows = []
    stat = defaultdict(Counter)
    for (arm, iid, x, q, _u, legs_flat, nlines), (txt, usage) in zip(jobs, out):
        ans, cites = split_answer(txt)
        gold = (qa.get(x["baseId"], {}).get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for f in sorted(legs_flat) if f in facts)[:200]
        # 自由措辞时引用可能夹在正文里：先按句剥掉引用从句，再统一抽编号
        cites_all = [int(v) for v in re.findall(r"F(\d+)", txt, re.I)] or cites
        ans = strip_cites(ans, gold) or ans
        cites = cites_all[:6]
        hit = any(c in legs_flat for c in cites)
        pure = bool(cites) and all(c in legs_flat for c in cites)
        ab = bool(ABSTAIN.search(ans)) or not ans
        rows.append({"arm": arm, "iid": iid, "kind": x["kind"], "q": q, "gold": gold,
                     "answer": ans, "cites": cites, "citeHit": hit, "citePure": pure,
                     "abstain": ab, "raw": txt[:200], "chars": sum(len(l) for l in
                     blocks[arm.split("/")[0]][arm.split("/")[1]][iid]),
                     "facts": nlines, "usage": usage})
        s = stat[arm]
        s["n"] += 1
        s["abstain"] += int(ab)
        s["cite_any"] += int(bool(cites))
        s["cite_hit"] += int(hit)
        s["cite_pure"] += int(pure)

    json.dump({"model": args.model, "arms": args.arms.split(","), "rows": rows},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n## 带编号材料：引用行为\n")
    print("| 臂 | 实例 | 给了引用 | 引用命中金标 | 引用全在金标内 | 沉默 |")
    print("|---|---|---|---|---|---|")
    for arm, c in stat.items():
        n = c["n"]
        print(f"| {arm} | {n} | {100*c['cite_any']/n:.0f}% | {c['cite_hit']} ({100*c['cite_hit']/n:.0f}%) | "
              f"{c['cite_pure']} ({100*c['cite_pure']/n:.0f}%) | {c['abstain']} ({100*c['abstain']/n:.0f}%) |")
    print(f"\n写出 {args.out}")


if __name__ == "__main__":
    main()
