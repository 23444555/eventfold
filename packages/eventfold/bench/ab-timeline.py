"""同夜/同型事件的时间线头：给块里的每一行抽出日期与时刻，按天聚成一段压缩时间线贴在材料开头。

动机（见 docs/EVALUATION.md 的 16 条错答案审计）：块里同时躺着 11-05 21:25 恢复、11-08 21:20 回滚 /
21:35 恢复、11-24 21:10-21:40 恢复、11-24 21:42「整整 40 分钟」，它们形状一样、动词一样，
模型把 11-24 的 40 分钟答给了「11 月 8 号那次」。时间线头不改任何一行，只是把这些时间按天排出来。

用法：
  python bench/ab-timeline.py --ids B03-3#v2,CT1#v1 --controls 30 --out bench/ab-answer-spine.json
"""
import argparse
import importlib.util
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ids = _load("ids_mod", os.path.join(HERE, "ab-answer-ids.py"))

DATE_PATTERNS = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
]
TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")


def day_of(text):
    for p in DATE_PATTERNS:
        m = p.search(text)
        if m:
            return f"{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def time_of(text):
    m = TIME_RE.search(text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def label_of(text, n=18):
    t = text
    for p in DATE_PATTERNS:
        t = p.sub("", t)
    t = TIME_RE.sub("", t)
    t = re.sub(r"[（(]\s*[)）]", "", t)
    t = re.sub(r"^(于|在)?\s*", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip(" -—:：,，、。")
    # 去掉开头的「某某于年月日」残留
    t = re.sub(r"^(王五|李四|王五|张三|肥鱼|张伟|主理人)", r"\1：", t)
    t = re.sub(r"^([^：]{1,4})：\s*(于|在)?[-—]?\s*", r"\1：", t)
    return t[:n]


def build_spine(lines, max_days=6, per_day=6, label_n=18):
    """把块里的行按天聚成一段压缩时间线；没有日期的行不进时间线。"""
    days = defaultdict(list)
    for line in lines:
        d = day_of(line)
        if not d:
            continue
        tm = time_of(line)
        days[d].append((tm or "99:99", label_of(line, label_n), line[:40]))
    if not days:
        return ""
    # 日期近的优先（块里的日期都在 2026 年内，按 MM-DD 排序即可；但首尾跨年不管）
    order = sorted(days)
    out = []
    for d in order[:max_days]:
        items = sorted(days[d], key=lambda x: x[0])
        # 同一时刻多行只留一条
        seen, keep = set(), []
        for tm, lab, _src in items:
            if tm in seen:
                continue
            seen.add(tm)
            keep.append(f"{tm if tm != '99:99' else '·'} {lab}")
            if len(keep) >= per_day:
                break
        out.append(f"- {d}：" + " ｜ ".join(keep))
    return "【同日时间线（按块内提及的日期与时刻排，未改动材料）】\n" + "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks-v4.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--cap", default="4800")
    ap.add_argument("--arm", default="vec")
    ap.add_argument("--sys", default="bestguess", choices=["bestguess", "none", "derive"])
    ap.add_argument("--ids", default="", help="逗号分隔的目标 iid，例如 B03-3#v2,CT1#v1")
    ap.add_argument("--controls", type=int, default=0, help="对照条数（从答案正确的实例里取，首 N 条）")
    ap.add_argument("--only-spine", action="store_true", help="对照臂：只给时间线、不给材料")
    ap.add_argument("--out", default=os.path.join(HERE, "ab-answer-spine.json"))
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    args = ap.parse_args()

    sys_prompt = {"bestguess": ids.SYS_BESTGUESS, "none": ids.SYS_NONE,
                  "derive": ids.SYS_DERIVE}[args.sys]
    ids.SYS = sys_prompt  # ids.call 读模块级 SYS
    key = ids.load_key()
    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))[args.cap][args.arm]
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {f["i"]: f["t"] for f in gen["corpus"]}
    by_text = defaultdict(list)
    for f in gen["corpus"]:
        by_text[ids.norm(f["t"])].append(f["i"])

    def fid_of(line, legs_flat):
        cand = by_text.get(ids.norm(line), [])
        if not cand:
            return None
        for c in cand:
            if c in legs_flat:
                return c
        return min(cand)

    # 选实例：目标 + 对照（对照取自 bestguess 已经答对的实例）
    want = [x.strip() for x in args.ids.split(",") if x.strip()]
    if args.controls:
        prev = json.load(open(os.path.join(HERE, "ab-rejudge-bg.json"), encoding="utf-8"))
        ok_ids = [r["iid"] for r in prev if r.get("llm") in ("ok", "partial")]
        step = max(1, len(ok_ids) // args.controls)
        want += ok_ids[::step][: args.controls]
    seen = set()
    want = [w for w in want if not (w in seen or seen.add(w))]

    jobs = []
    for iid in want:
        if iid not in blocks or iid not in inst:
            print("skip", iid)
            continue
        x = inst[iid]
        lines = blocks[iid]
        legs_flat = {f for leg in x["legs"] for f in leg}
        labelled = []
        for line in lines:
            fid = fid_of(line, legs_flat)
            labelled.append(f"[F{fid}] {line}" if fid else line)
        spine = build_spine(lines)
        body = ("【材料】\n" + "\n".join(f"- {l}" for l in labelled)) if not args.only_spine \
            else "【材料】（只有时间线）"
        user = (spine + "\n" + body if spine else body) + f"\n\n【问题】{x['q']}\n\n【回答】"
        jobs.append((iid, x, user, legs_flat, len(lines), len(spine)))

    print(f"{len(jobs)} 次请求｜cap {args.cap}/{args.arm}｜提示 {args.sys}｜{args.model}", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        out = list(ex.map(lambda j: ids.call(args.base, key, args.model, j[2]), jobs))
    tin = sum(u.get("prompt_tokens", 0) for _t, u in out)
    print(f"完成 {len(out)}｜{time.time()-t0:.0f}s｜token 入 {tin}", flush=True)

    rows = []
    for (iid, x, user, legs_flat, nlines, spine_len), (txt, usage) in zip(jobs, out):
        ans, cites = ids.split_answer(txt)
        gold = (qa.get(x["baseId"], {}).get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for f in sorted(legs_flat) if f in facts)[:200]
        ans = ids.strip_cites(ans, gold) or ans
        ab = bool(ids.ABSTAIN.search(ans)) or not ans
        rows.append({"arm": f"{args.cap}/{args.arm}", "iid": iid, "kind": x["kind"], "q": x["q"],
                     "gold": gold, "answer": ans, "cites": cites, "citeHit": False, "citePure": False,
                     "abstain": ab, "raw": txt[:200], "chars": 0, "facts": nlines,
                     "spineChars": spine_len, "usage": usage})
        print(f"  {iid:14s} spine={spine_len:4d} abstain={int(ab)} {ans[:40]}", flush=True)
    json.dump({"model": args.model, "arms": [f"{args.cap}/{args.arm}"], "rows": rows},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出", args.out)


if __name__ == "__main__":
    main()
