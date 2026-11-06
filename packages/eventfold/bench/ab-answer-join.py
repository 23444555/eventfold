"""把「证据齐不齐」和「答对没答对」对起来看，并抽查判分是否冤。"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(p):
    return json.load(open(p, encoding="utf-8"))


ans = load(os.path.join(HERE, "ab-answer-result.json"))
cap = load(os.path.join(HERE, "ab-cap-result.json"))

# cap -> arm -> iid -> {strict, legs}
cov = {}
for cap_key, per_arm in cap["perQuery"].items():
    for arm, rows in per_arm.items():
        cov.setdefault(cap_key, {}).setdefault(arm, {})
        for r in rows:
            cov[cap_key][arm][r["iid"]] = (r["strict"], r["legs"])

rows = ans["rows"]
by_arm = {}
for r in rows:
    by_arm.setdefault(r["arm"], []).append(r)

print("## 证据齐不齐 × 答对没答对\n")
print("| 臂 | 腿全齐的题 | 其中答对 | 腿不齐的题 | 其中答对 | 齐/不齐差距 |")
print("|---|---|---|---|---|---|")
for arm in ans["arms"]:
    cap_key, name = arm.split("/") if "/" in arm else (None, None)
    if not cap_key:
        continue
    rs = by_arm[arm]
    full = [r for r in rs if cov[cap_key][name][r["iid"]][0] == cov[cap_key][name][r["iid"]][1]]
    part = [r for r in rs if cov[cap_key][name][r["iid"]][0] < cov[cap_key][name][r["iid"]][1]]
    fo = sum(1 for r in full if r["verdict"] == "ok")
    po = sum(1 for r in part if r["verdict"] == "ok")
    fa = sum(1 for r in full if r["verdict"] == "abstain")
    pa = sum(1 for r in part if r["verdict"] == "abstain")
    print(f"| {arm} | {len(full)} | {fo} ({100*fo/max(1,len(full)):.0f}%) "
          f"| {len(part)} | {po} ({100*po/max(1,len(part)):.0f}%) "
          f"| {100*fo/max(1,len(full)) - 100*po/max(1,len(part)):+.0f} pt "
          f"（沉默 {fa}/{len(full)} vs {pa}/{len(part)}） |")

print("\n## 答错/沉默抽查（4800/vec，前 12 条）\n")
n = 0
for r in by_arm["4800/vec"]:
    if r["verdict"] == "ok":
        continue
    print(f"- {r['iid']} [{r['verdict']}] Q: {r['q'][:40]}\n  G: {str(r['gold'])[:70]}\n  A: {str(r['answer'])[:70]}")
    n += 1
    if n >= 12:
        break
