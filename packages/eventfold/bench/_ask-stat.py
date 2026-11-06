"""ab-ask 结果统计：配对 McNemar、按「是否提问」分层、无效提问那批的效率。"""
import json
import sys
from collections import Counter, defaultdict
from math import comb

HERE = r"C:\Users\ASUS\Documents\deepseek-harness\default-workspace\eventfold\bench"
ASK = json.load(open(HERE + r"\ab-ask.json", encoding="utf-8"))
JUD = json.load(open(HERE + r"\ab-ask-rejudge.json", encoding="utf-8"))

verdict = {}
for r in JUD:
    verdict[(r["arm"], r["iid"])] = r["llm"]
rows = {(r["arm"], r["iid"]): r for r in ASK["rows"]}


def good(v):
    return v in ("ok", "partial")


def mcnemar(a_arm, b_arm, label):
    a = b = both = neither = 0
    for (arm, iid), r in rows.items():
        if arm != a_arm:
            continue
        va, vb = good(verdict.get((a_arm, iid), "")), good(verdict.get((b_arm, iid), ""))
        if va and vb:
            both += 1
        elif va:
            a += 1
        elif vb:
            b += 1
        else:
            neither += 1
    n = a + b
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(a, b) + 1)) / 2 ** n)
    print(f"{label}: {a_arm} 独赢 {a} / {b_arm} 独赢 {b} / 都赢 {both} / 都错 {neither} → 精确 p={p:.3f}")
    return a, b, p


print("## 配对（ok+partial 算对，n=172）")
mcnemar("ask", "single", "ask vs single")
mcnemar("askVague", "single", "askVague vs single")
mcnemar("ask", "askVague", "ask vs askVague")

print("\n## 分层：模型真的提问了的那批")
for arm in ("ask", "askVague"):
    ids_asked = [iid for (a, iid), r in rows.items() if a == arm and r["asked"]]
    print(f"\n[{arm}] 提问 {len(ids_asked)} 条")
    grid = Counter()
    cov = Counter()
    for iid in ids_asked:
        v0 = verdict.get(("single", iid), "")
        v1 = verdict.get((arm, iid), "")
        grid[(good(v0), good(v1))] += 1
        c = round(rows[(arm, iid)]["covered"], 2)
        cov[(c, good(v1))] += 1
    print("  single 对/错 × 提问后 对/错：",
          {f"{'对' if k[0] else '错'}→{'对' if k[1] else '错'}": v for k, v in sorted(grid.items())})
    print("  材料覆盖率 × 提问后结果：",
          {f"cov{k[0]}:{'对' if k[1] else '错'}": v for k, v in sorted(cov.items())})
    print("  提问后仍错：", [iid for iid in ids_asked if not good(verdict.get((arm, iid), ""))][:12])

print("\n## 未提问那批（ask 臂）：提示词改动有没有副作用")
ids_no = [iid for (a, iid), r in rows.items() if a == "ask" and not r["asked"]]
g = Counter((good(verdict.get(("single", iid), "")), good(verdict.get(("ask", iid), ""))) for iid in ids_no)
print(f"  n={len(ids_no)}", {f"{'对' if k[0] else '错'}→{'对' if k[1] else '错'}": v for k, v in sorted(g.items())})

print("\n## 无效提问（金标不全在材料）里发生了什么")
for arm in ("ask", "askVague"):
    ids_u = [iid for (a, iid), r in rows.items() if a == arm and r["asked"] and r["covered"] < 1.0]
    print(f"  {arm}: {len(ids_u)} 条，提问后答对 {sum(good(verdict.get((arm, iid), '')) for iid in ids_u)}"
          f"（同一批 single 答对 {sum(good(verdict.get(('single', iid), '')) for iid in ids_u)}）")

print("\n## 代价（token，含追问两轮）")
for arm in ("single", "ask", "askVague"):
    t = [r["tokens"] for (a, _i), r in rows.items() if a == arm]
    print(f"  {arm}: 平均 {sum(t)/len(t):.0f} tok/题，总计 {sum(t):,}")

print("\n## 提问样例（ask 臂，含用户回答与结论）")
n = 0
for (a, iid), r in rows.items():
    if a == "ask" and r["asked"] and n < 10:
        n += 1
        print(f"- Q: {r['q'][:40]}\n  ASK: {r['question']}\n  用户: {r['clarify']}\n  "
              f"答: {r['answer'][:40]} [{verdict.get((a, iid))}]  single: [{verdict.get(('single', iid))}]"
              f" cov={r['covered']:.2f}")
