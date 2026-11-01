"""把 沉默重试 的判分结果按 策略 × 分组 表出来：多少翻成对/错。"""
import io
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
rows = json.load(io.open(os.path.join(HERE, "ab-rejudge-retry.json"), encoding="utf-8"))
raw = json.load(io.open(os.path.join(HERE, "ab-abstain-retry.json"), encoding="utf-8"))["rows"]
grp = {(r["arm"], r["iid"]): r["grp"] for r in raw}

tab = defaultdict(Counter)
for r in rows:
    pol = r["arm"].split("/", 1)[1]
    g = grp.get((r["arm"], r["iid"]), "?")
    tab[(g, pol)][r["llm"]] += 1

print("| 组 | 策略 | n | ok | ok+partial | wrong | abstain |")
print("|---|---|---|---|---|---|---|")
for g in ("target", "control"):
    for pol in ("base", "derive", "bestguess"):
        c = tab.get((g, pol))
        if not c:
            continue
        n = sum(c.values())
        print("| %s | %s | %d | %d (%.0f%%) | %d (%.0f%%) | %d | %d |"
              % (g, pol, n, c["ok"], 100 * c["ok"] / n, c["ok"] + c["partial"],
                 100 * (c["ok"] + c["partial"]) / n, c["wrong"], c["abstain"]))

# 逐条：target 组在 derive 下变成什么
print("\n## 27 条沉默在 derive / bestguess 下的结局")
by = defaultdict(dict)
for r in rows:
    pol = r["arm"].split("/", 1)[1]
    if grp.get((r["arm"], r["iid"])) == "target":
        by[r["iid"]][pol] = r
for iid, d in by.items():
    base = d.get("base", {})
    line = ["%-12s base=%-8s" % (iid, base.get("llm", "-"))]
    for pol in ("derive", "bestguess"):
        r = d.get(pol)
        if r:
            line.append("%s=%-8s %s" % (pol, r["llm"], (r.get("answer") or "")[:34]))
    print("  " + " | ".join(line))
    print("      gold: %s" % (base.get("gold") or "")[:90])
