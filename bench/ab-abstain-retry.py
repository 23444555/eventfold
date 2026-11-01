"""沉默审计的第二步：材料里明明有金标，为什么还说「不知道」？

对 27 条沉默实例（其中 19 条材料全齐）再问一遍，换两种「作答契约」：
  base      —— 原提示词（复现，应仍沉默）
  derive    —— 允许推算/换算（「能据此算出来就作答」）
  bestguess —— 只要材料里有相关句子就必须给最像的答案（不许因措辞不同而弃答）

另外抽 30 条原本答对的实例做对照，检查新契约会不会把对的搞错。

用法：
  python bench/ab-abstain-retry.py --audit bench/ab-abstain-audit.json --control 30 --conc 12
"""
import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import importlib.util

spec = importlib.util.spec_from_file_location("answerids", os.path.join(HERE, "ab-answer-ids.py"))
ids_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ids_mod)

BASE_SYS = ids_mod.SYS_NONE

SYS = {
    "base": BASE_SYS,
    "derive": ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
               "材料里的句子可能没有直接写出答案，但能据此算出来、比出来、或看出结论时，就作答。"
               "只有当材料里完全没有相关句子时才回「不知道」。"),
    "bestguess": ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
                  "只要材料里有和问题相关的句子，就给出最可能的答案——措辞不同、需要换算、需要取某个数字，"
                  "都照答；只有当材料里完全没有相关句子时才回「不知道」。"),
}


import urllib.request


def call_sys(base, key, model, user, sys_prompt, max_tokens=80, timeout=90):
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": max_tokens,
                       "thinking": {"type": "disabled"},
                       "messages": [{"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            return d["choices"][0]["message"]["content"].strip(), d.get("usage", {})
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5)
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default=os.path.join(HERE, "ab-abstain-audit.json"))
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks-v4.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--judge", default=os.path.join(HERE, "ab-rejudge-ids-none.json"))
    ap.add_argument("--arm", default="4800/vec")
    ap.add_argument("--policies", default="base,derive,bestguess")
    ap.add_argument("--control", type=int, default=30, help="抽多少条原本答对的做对照")
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(HERE, "ab-abstain-retry.json"))
    args = ap.parse_args()

    cap, arm = args.arm.split("/")
    key = ids_mod.load_key()
    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {f["i"]: f["t"] for f in gen["corpus"]}
    lines_by_iid = blocks[cap][arm]

    by_text = defaultdict(list)
    for f in gen["corpus"]:
        by_text[ids_mod.norm(f["t"])].append(f["i"])

    def material(iid):
        x = inst[iid]
        legs_flat = {f for leg in x["legs"] for f in leg}
        labelled = []
        for line in lines_by_iid[iid]:
            cand = by_text.get(ids_mod.norm(line), [])
            fid = None
            if cand:
                fid = next((c for c in cand if c in legs_flat), min(cand))
            labelled.append(f"[F{fid}] {line}" if fid else line)
        user = "【材料】\n" + "\n".join(f"- {l}" for l in labelled) + f"\n\n【问题】{x['q']}\n\n【回答】"
        return x, legs_flat, user

    audit = json.load(io.open(args.audit, encoding="utf-8"))
    target = [a["iid"] for a in audit]
    judged = {(r["iid"], r["arm"]): r["llm"] for r in json.load(open(args.judge, encoding="utf-8"))}
    pool = [iid for (iid, a), v in judged.items()
            if a == args.arm and v in ("ok", "partial") and iid not in target]
    pool.sort()
    step = max(1, len(pool) // max(args.control, 1))
    control = pool[::step][: args.control]

    jobs = []
    for pol in args.policies.split(","):
        for iid in target:
            jobs.append((pol, iid, "target"))
        for iid in control:
            jobs.append((pol, iid, "control"))
    print("%d 次请求｜沉默 %d + 对照 %d｜策略 %s" % (len(jobs), len(target), len(control), args.policies),
          flush=True)

    def run(job):
        pol, iid, grp = job
        x, legs_flat, user = material(iid)
        txt, usage = call_sys(args.base, key, args.model, user, SYS[pol])
        return pol, iid, grp, txt, usage

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        done = list(ex.map(run, jobs))
    print("完成 %d｜%.0fs" % (len(done), time.time() - t0), flush=True)

    rows = []
    for pol, iid, grp, txt, usage in done:
        x = inst[iid]
        legs_flat = {f for leg in x["legs"] for f in leg}
        gold = (qa.get(x["baseId"], {}).get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for f in sorted(legs_flat) if f in facts)[:200]
        ans = ids_mod.strip_cites(txt, gold) or txt
        ab = bool(ids_mod.ABSTAIN.search(ans)) or not ans
        rows.append({"arm": "retry/" + pol, "iid": iid, "grp": grp, "kind": x["kind"], "q": x["q"],
                     "gold": gold, "answer": ans, "abstain": ab, "raw": txt[:200], "usage": usage})

    json.dump({"model": args.model, "arms": ["retry/" + p for p in args.policies.split(",")], "rows": rows},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出", args.out)

    # 先看「有没有变成不沉默」，判分交给 ab-rejudge-llm.py
    for pol in args.policies.split(","):
        for grp in ("target", "control"):
            c = Counter(r["abstain"] for r in rows if r["arm"] == "retry/" + pol and r["grp"] == grp)
            n = c[True] + c[False]
            print("  %-9s %-7s n=%2d 仍沉默 %2d (%.0f%%)" % (pol, grp, n, c[True],
                                                            100 * c[True] / max(n, 1)))


if __name__ == "__main__":
    main()
