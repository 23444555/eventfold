"""两段式：先让它自己挑编号（第一段），再只拿挑中的行回答（第二段）。

第一段是「能力不是义务」的落地：模型只被要求指认，不被要求答题；第二段它拿到的材料从
4,800 字缩到几百字，看注意力是否更集中。

对照 = 同一批材料、同一问题、单段直接回答（`bench/ab-answer-ids.py --close none`，
4800/vec 77.3% ok+partial）。

指标：答对率、沉默率、选择命中（挑中的 id 里有没有金标）、选择纯净（全都属于金标）、
两段各自的延迟与 token。

用法：
  python bench/ab-answer-2stage.py --arms 4800/vec --topk 12 --cap2 1200 --conc 12
"""
import argparse
import importlib.util
import json
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))

_spec = importlib.util.spec_from_file_location("ids", os.path.join(HERE, "ab-answer-ids.py"))
ids_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ids_mod)

SYS_PICK = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。"
            "请从材料里挑出回答下面这个问题会用到的行，只回编号，用逗号分开，"
            "最多 {k} 个，不要回答、不要解释；材料里没有就用到的信息就回「无」。")
SYS_ANS = ("你在读一小段带编号的记忆材料。只根据材料回答，不超过 20 字。"
           "材料里没有这条信息就只回「不知道」。")


def call_raw(base, key, model, sys_prompt, user, max_tokens=200, timeout=90):
    body = {"model": model, "messages": [{"role": "system", "content": sys_prompt},
                                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            return (d["choices"][0]["message"].get("content") or "").strip(), (d.get("usage") or {})
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return "", {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks-v4.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-answer-2stage.json"))
    ap.add_argument("--arms", default="4800/vec")
    ap.add_argument("--topk", type=int, default=12, help="第一段最多挑几条")
    ap.add_argument("--cap2", type=int, default=1600, help="第二段材料字数上限（0=不限）")
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    key = ids_mod.load_key()
    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {f["i"]: f["t"] for f in gen["corpus"]}

    by_text = defaultdict(list)
    for f in gen["corpus"]:
        by_text[ids_mod.norm(f["t"])].append(f["i"])

    def fid_of(line, legs_flat):
        cand = by_text.get(ids_mod.norm(line), [])
        if not cand:
            return None
        for c in cand:
            if c in legs_flat:
                return c
        return min(cand)

    # 组装：每个实例的带编号材料
    jobs = []
    for arm in args.arms.split(","):
        cap, name = arm.split("/")
        for iid, lines in blocks[cap][name].items():
            x = inst[iid]
            legs_flat = {f for leg in x["legs"] for f in leg}
            labelled = []
            for line in lines:
                fid = fid_of(line, legs_flat)
                labelled.append((fid, line))
            jobs.append({"arm": arm, "iid": iid, "x": x, "q": x["q"],
                         "lines": labelled, "legs": legs_flat})
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"{len(jobs)} 个实例 × 2 段｜臂 {args.arms}｜topk {args.topk}｜cap2 {args.cap2}", flush=True)

    # ── 第一段：只挑编号
    t0 = time.time()
    sys1 = SYS_PICK.format(k=args.topk)

    def stage1(j):
        mat = "\n".join(f"- [F{fid}] {line}" if fid else f"- {line}" for fid, line in j["lines"])
        user = f"【材料】\n{mat}\n\n【问题】{j['q']}\n\n【用到的编号】"
        txt, usage = call_raw(args.base, key, args.model, sys1, user, max_tokens=160)
        picked = [int(v) for v in re.findall(r"F(\d+)", txt, re.I)][: args.topk]
        return txt, picked, usage

    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        s1 = list(ex.map(stage1, jobs))
    t1 = time.time() - t0
    print(f"第一段完成 {len(jobs)}｜{t1:.0f}s", flush=True)

    # ── 第二段：只喂挑中的行
    picked_any = 0
    for j, (txt, picked, _u) in zip(jobs, s1):
        j["pick_raw"] = txt[:120]
        j["picked"] = picked
        line_by_id = {fid: line for fid, line in j["lines"] if fid}
        lines = [line_by_id[i] for i in picked if i in line_by_id]
        j["subset"] = lines
        picked_any += int(bool(lines))
        j["selHit"] = any(i in j["legs"] for i in picked)
        j["selPure"] = bool(picked) and all(i in j["legs"] for i in picked)

    def stage2(j):
        body = "\n".join(f"- [F{fid}] {line}" for fid, line in j["lines"] if fid in j["picked"])
        if args.cap2:
            cut, used = [], 0
            for ln in body.split("\n"):
                if used + len(ln) + 1 > args.cap2:
                    break
                cut.append(ln)
                used += len(ln) + 1
            body = "\n".join(cut)
        user = f"【材料】\n{body}\n\n【问题】{j['q']}\n\n【回答】"
        return call_raw(args.base, key, args.model, SYS_ANS, user, max_tokens=80)

    t2 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        s2 = list(ex.map(stage2, jobs))
    print(f"第二段完成 {len(jobs)}｜{time.time()-t2:.0f}s", flush=True)

    rows = []
    stat = defaultdict(Counter)
    for j, (txt1, picked, u1), (txt2, u2) in zip(jobs, s1, s2):
        ans, _cites = ids_mod.split_answer(txt2)
        gold = (qa.get(j["x"]["baseId"], {}).get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for f in sorted(j["legs"]) if f in facts)[:200]
        ans = ids_mod.strip_cites(ans, gold) or ans
        ab = bool(ids_mod.ABSTAIN.search(ans)) or not ans
        rows.append({"arm": j["arm"], "iid": j["iid"], "kind": j["x"]["kind"], "q": j["q"],
                     "gold": gold, "answer": ans, "abstain": ab,
                     "picked": j["picked"], "selHit": j["selHit"], "selPure": j["selPure"],
                     "nPicked": len(j["picked"]), "nSubset": len(j["subset"]),
                     "chars": sum(len(l) + 1 for l in j["subset"]),
                     "usage": {"s1": u1, "s2": u2}})
        c = stat[j["arm"]]
        c["n"] += 1
        c["abstain"] += int(ab)
        c["selHit"] += int(j["selHit"])
        c["selPure"] += int(j["selPure"])
        c["empty"] += int(not j["subset"])

    json.dump({"model": args.model, "arms": args.arms.split(","), "topk": args.topk,
               "rows": rows}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n## 两段式：选择行为\n")
    print("| 臂 | 实例 | 挑空 | 选择命中金标 | 选择全在金标内 | 平均挑中 | 第二段字数 | 沉默 |")
    print("|---|---|---|---|---|---|---|---|")
    for arm, c in stat.items():
        n = c["n"]
        nc = [r["nPicked"] for r in rows if r["arm"] == arm]
        ch = [r["chars"] for r in rows if r["arm"] == arm]
        print(f"| {arm} | {n} | {c['empty']} | {c['selHit']} ({100*c['selHit']/n:.0f}%) | "
              f"{c['selPure']} ({100*c['selPure']/n:.0f}%) | {sum(nc)/n:.1f} | {sum(ch)/n:.0f} | "
              f"{c['abstain']} ({100*c['abstain']/n:.0f}%) |")
    print(f"\n写出 {args.out}")


if __name__ == "__main__":
    main()
