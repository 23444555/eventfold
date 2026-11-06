"""改写题到底怎么了？——把「改写」单独拉出来做三个判据。

用户怀疑：改写题的低分可能是题本身的问题，不是系统的问题。这里用三件可测的事回应：

1. 证据齐不齐 × 答对：把 base / variant 分开。如果 variant 在「腿全齐」时也答不对，
   说明题在给人材料时也不可答（题坏）；如果齐了就答对，说明丢在检索（系统问题）。
2. 复述度：variant 的问句与金标事实的字符 bigram 重合度，和 base 比。改写本来就会掉，
   但掉到某个程度以下，连人都找不回来。
3. 歧义度：variant 的金标事实，在库里有多少条「同样像答案」的干扰项——
   用「与金标共享 ≥60% 的 3-gram」的条数算，条数越多，题越不唯一。

用法：python bench/_variant_diag.py
"""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def bigrams(s):
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jac(a, b):
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def grams(s, n=3):
    s = norm(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def main():
    gen = json.load(open(os.path.join(HERE, "ab-gen.json"), encoding="utf-8"))
    ans = json.load(open(os.path.join(HERE, "ab-answer-result.json"), encoding="utf-8"))
    cap = json.load(open(os.path.join(HERE, "ab-cap-result.json"), encoding="utf-8"))

    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {c["i"]: c["t"] for c in gen["corpus"]}

    # --- 1. 分 base / variant 的「齐了就答对」表 ---
    arm = "4800/vec"
    pq = {r["iid"]: r for r in cap["perQuery"]["4800"]["vec"]}
    rows = [r for r in ans["rows"] if r["arm"] == arm]
    print(f"## 1. {arm}：证据齐不齐 × 答对（base / variant 分开）\n")
    print("| 类型 | 腿全齐 | 齐了答对 | 齐了沉默 | 腿上不齐 | 不齐也答对 |")
    print("|---|---|---|---|---|---|")
    for kind, label in (("base", "原问"), ("variant", "改写")):
        sel = [r for r in rows if r["kind"] == kind]
        done = [r for r in sel if pq[r["iid"]]["legs"] == pq[r["iid"]]["missing"] is not None
                and not pq[r["iid"]]["missing"]]
        miss = [r for r in sel if pq[r["iid"]]["missing"]]
        ok = sum(1 for r in done if r["verdict"] == "ok")
        ab = sum(1 for r in done if r["verdict"] == "abstain")
        ok2 = sum(1 for r in miss if r["verdict"] == "ok")
        print(f"| {label} | {len(done)} | {ok} ({100*ok/max(len(done),1):.0f}%) | "
              f"{ab} | {len(miss)} | {ok2} ({100*ok2/max(len(miss),1):.0f}%) |")

    # --- 2. 复述度：问句与金标事实的 bigram 重合 ---
    print("\n## 2. 问句 ↔ 金标事实 的字符 bigram 重合度\n")
    for kind, label in (("base", "原问"), ("variant", "改写")):
        vals = []
        for x in gen["instances"]:
            if x["kind"] != kind:
                continue
            gold = " ".join(facts[f] for leg in x["legs"] for f in leg if f in facts)
            vals.append(jac(x["q"], gold))
        vals.sort()
        n = len(vals)
        print(f"- {label}：中位 {vals[n//2]:.3f}｜p90 {vals[int(n*0.9)]:.3f}｜"
              f"完全无重合 {sum(1 for v in vals if v == 0)}/{n}")

    # --- 3. 歧义度：金标事实有多少「同样像答案」的干扰项 ---
    print("\n## 3. 金标的歧义度（库内与金标共享 ≥60% 的 3-gram 的条数）\n")
    fact_grams = {i: grams(t) for i, t in facts.items()}
    for kind, label in (("base", "原问"), ("variant", "改写")):
        vals = []
        for x in gen["instances"]:
            if x["kind"] != kind:
                continue
            for leg in x["legs"]:
                for f in leg[:1]:
                    if f not in fact_grams:
                        continue
                    g = fact_grams[f]
                    if not g:
                        continue
                    rivals = 0
                    for i, gg in fact_grams.items():
                        if i == f:
                            continue
                        ov = len(g & gg) / len(g | gg)
                        if ov >= 0.6:
                            rivals += 1
                    vals.append(rivals)
        if vals:
            vals.sort()
            print(f"- {label}：中位 {vals[len(vals)//2]}｜p90 {vals[int(len(vals)*0.9)]}｜"
                  f"≥1 个干扰项 {sum(1 for v in vals if v)}/{len(vals)}")

    # --- 4. 抽几个改写题看人能不能答 ---
    print("\n## 4. 抽样：改写题 + 金标（看人的判断）\n")
    shown = 0
    for x in gen["instances"]:
        if x["kind"] != "variant" or shown >= 6:
            continue
        base = inst.get(x["baseId"], {})
        r = pq.get(x["iid"], {})
        if not r.get("missing"):
            continue
        print(f"**{x['iid']}**（原题 {x['baseId']}）")
        print(f"- 原问：{base.get('q','')}")
        print(f"- 改写：{x['q']}")
        gold = [facts[f] for leg in x["legs"] for f in leg if f in facts]
        for g in gold[:2]:
            print(f"- 金标：{g[:90]}")
        print(f"- 缺腿 {len(r['missing'])} 条：{r['missing'][:3]}\n")
        shown += 1


if __name__ == "__main__":
    main()
