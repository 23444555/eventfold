"""按「腿是否全齐」分解答题结果：覆盖率 -> 答对率的转换。"""
import io, json, os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def load(n):
    return json.load(io.open(os.path.join(HERE, n), encoding="utf-8"))


sets = [
    ("4800/vec 全给（基线）", "ab-rejudge-ids-none.json", "4800", "vec"),
    ("4800/sib25 带编号", "ab-rejudge-sib25-ids.json", "4800", "sib25"),
    ("1600 截断对照", "ab-rejudge-capcontrol.json", "1600", None),
]

cap = load("ab-cap-v4.json")
for name, jf, capkey, arm in sets:
    if not os.path.exists(os.path.join(HERE, jf)):
        print(name, "缺", jf)
        continue
    judged = {(r["iid"], r["arm"]): r["llm"] for r in load(jf)}
    per = {}
    if arm:
        for e in cap["perQuery"].get(capkey, {}).get(arm, []):
            per[e["iid"]] = e
    tot = Counter()
    for (iid, a), v in judged.items():
        ok = v in ("ok", "partial")
        e = per.get(iid)
        if e is None:
            continue
        full = e.get("strict") == e.get("legs")  # strict 是「严格命中的腿数」，全齐 = 等于腿总数
        tot[("full" if full else "miss", "n")] += 1
        if ok:
            tot[("full" if full else "miss", "ok")] += 1
        if v == "abstain":
            tot[("full" if full else "miss", "abst")] += 1
    nf, nm = tot[("full", "n")], tot[("miss", "n")]
    if nf + nm:
        print("%-22s 全齐 %3d -> 答对 %3d (%.0f%%), 沉默 %2d | 缺腿 %3d -> 答对 %3d (%.0f%%), 沉默 %2d"
              % (name, nf, tot[("full", "ok")], 100 * tot[("full", "ok")] / max(nf, 1), tot[("full", "abst")],
                 nm, tot[("miss", "ok")], 100 * tot[("miss", "ok")] / max(nm, 1), tot[("miss", "abst")]))
