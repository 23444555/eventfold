"""四臂合并统计：single / ask / askVague / askGated 对 single 的配对比较。"""
import json
from collections import Counter
from math import comb

B = r"C:\Users\ASUS\Documents\deepseek-harness\default-workspace\eventfold\bench"
rows = {}
for f in (r"\ab-ask.json", r"\ab-ask-gated.json"):
    for r in json.load(open(B + f, encoding="utf-8"))["rows"]:
        rows[(r["arm"], r["iid"])] = r
verdict = {}
for f in (r"\ab-ask-rejudge.json", r"\ab-ask-gated-rejudge.json"):
    for r in json.load(open(B + f, encoding="utf-8")):
        verdict[(r["arm"], r["iid"])] = r["llm"]


def good(v):
    return v in ("ok", "partial")


ARMS = ["single", "ask", "askVague", "askGated"]
print("## 总表（n=172）\n")
print("| 臂 | 提问率 | ok | ok+partial | wrong | abstain | 平均 tok/题 |")
print("|---|---|---|---|---|---|---|")
for a in ARMS:
    ids = [i for (arm, i) in rows if arm == a]
    c = Counter(verdict.get((a, i), "") for i in ids)
    tok = sum(rows[(a, i)]["tokens"] for i in ids) / len(ids)
    ask = sum(rows[(a, i)]["asked"] for i in ids)
    print(f"| {a} | {100*ask/len(ids):.0f}% | {c['ok']}/{len(ids)} ({100*c['ok']/len(ids):.1f}%) | "
          f"{c['ok']+c['partial']} ({100*(c['ok']+c['partial'])/len(ids):.1f}%) | {c['wrong']} | "
          f"{c['abstain']} | {tok:.0f} |")

print("\n## 对 single 的配对（ok+partial）\n")
for a in ARMS[1:]:
    win = lose = both = none = 0
    for (arm, i) in rows:
        if arm != a:
            continue
        va, vb = good(verdict.get((a, i), "")), good(verdict.get(("single", i), ""))
        if va and vb:
            both += 1
        elif va:
            win += 1
        elif vb:
            lose += 1
        else:
            none += 1
    n = win + lose
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(win, lose) + 1)) / 2 ** n)
    print(f"- {a}: 赢 {win} / 输 {lose} / 平对 {both} / 平错 {none} → p={p:.3f}")

print("\n## askGated 翻车的例子（gated 错 / single 对）")
n = 0
for (arm, i), r in rows.items():
    if arm == "askGated" and not good(verdict.get((arm, i), "")) and good(verdict.get(("single", i), "")):
        n += 1
        if n <= 8:
            print(f"- Q: {r['q'][:44]}\n  single: {rows[('single', i)]['answer'][:40]} | gated: {r['answer'][:40]}"
                  f" (asked={r['asked']}, cov={r['covered']:.2f})")
print(f"  共 {n} 例")

print("\n## askGated 问到点子上的例子（gated 对 / single 错）")
n = 0
for (arm, i), r in rows.items():
    if arm == "askGated" and good(verdict.get((arm, i), "")) and not good(verdict.get(("single", i), "")):
        n += 1
        if n <= 8:
            print(f"- Q: {r['q'][:44]} | ASK: {r['question'][:44]} | 用户: {r['clarify'][:30]} | "
                  f"gated: {r['answer'][:36]} | single: {rows[('single', i)]['answer'][:30]}")
print(f"  共 {n} 例")
