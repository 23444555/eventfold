"""对比：无编号基线 / 编号+不提引用 / 编号+bestguess 许可式契约（同一批 172 实例、同一裁判）。"""
import io
import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def load(n):
    return json.load(io.open(os.path.join(HERE, n), encoding="utf-8"))


def summary(f, label):
    c = Counter(r["llm"] for r in load(f))
    n = sum(c.values())
    print("%-26s n=%3d ok %3d (%5.1f%%) ok+partial %3d (%5.1f%%) partial %2d wrong %2d abstain %2d"
          % (label, n, c["ok"], 100 * c["ok"] / n, c["ok"] + c["partial"],
             100 * (c["ok"] + c["partial"]) / n, c["partial"], c["wrong"], c["abstain"]))
    return c, n


print("## 三种答题契约（172 实例，材料同为 4,800/vec，句首编号）")
summary("ab-rejudge-v4.json", "无编号（旧基线）")
summary("ab-rejudge-ids-none.json", "编号+不提引用")
summary("ab-rejudge-bg.json", "编号+bestguess 许可式")

# 按 kind / 题型拆 bestguess vs ids-none
gen = load("ab-gen-v4.json")
inst = {x["iid"]: x for x in gen["instances"]}


def split(f):
    tab = defaultdict(Counter)
    for r in load(f):
        it = inst.get(r["iid"], {})
        tab[(it.get("kind"), it.get("type"))][r["llm"]] += 1
    return tab


a = split("ab-rejudge-ids-none.json")
b = split("ab-rejudge-bg.json")
print("\n## 按 kind × 题型（ok+partial / n）")
print("| kind | 题型 | 编号不提引用 | bestguess |")
print("|---|---|---|---|")
keys = sorted(a.keys(), key=lambda k: (str(k[0]), str(k[1])))
for k in keys:
    if k not in b:
        continue
    ca, cb = a[k], b[k]
    na, nb = sum(ca.values()), sum(cb.values())
    print("| %s | %s | %d/%d | %d/%d |" % (k[0], k[1], ca["ok"] + ca["partial"], na,
                                           cb["ok"] + cb["partial"], nb))
