"""把「改写是否还是同一题」接回成绩表：同一批答案，按 same/partial/different 重算。

只用已有 JSON，不调模型、不重跑：
  _variant-equiv.json    每条改写的 same/partial/different
  ab-cap-result.json     4800/vec 的 strict / legs / missing
  ab-answer-result.json  4800/vec 的 verdict
  ab-hyde.json           rankQ / rankH / rankRRF

用法：python bench/_variant_impact.py
"""
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    return json.load(open(os.path.join(HERE, name), encoding="utf-8"))


def main():
    eq = {r["iid"]: r["verdict"] for r in load("_variant-equiv.json")}
    cap = load("ab-cap-result.json")["perQuery"]["4800"]["vec"]
    ans = {r["iid"]: r for r in load("ab-answer-result.json")["rows"] if r["arm"] == "4800/vec"}
    hy = {r["iid"]: r for r in load("ab-hyde.json")["rows"]}

    groups = {"base": [r["iid"] for r in cap if r["kind"] == "base"],
              "same": [], "partial": [], "different": []}
    for r in cap:
        if r["kind"] == "variant":
            groups[eq.get(r["iid"], "unknown")].append(r["iid"])
    for k in groups:
        groups[k] = [i for i in groups[k] if i in ans]

    print("## 改写按「还是不是同一题」重算（4800/vec，同一批跑分）\n")
    print("| 组 | 实例 | 召回全铺齐 | 腿齐 | 答对 | 沉默 | 答错 | 名次中位(q) | 名次中位(hypo) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for k in ("base", "same", "partial", "different"):
        ids = groups[k]
        if not ids:
            continue
        rows = [r for r in cap if r["iid"] in set(ids)]
        full = sum(1 for r in rows if r["strict"] == 1)
        legs = sum(r["legs"] for r in rows)
        legs_total = sum(r["legs"] + len(r["missing"]) for r in rows)
        ok = sum(1 for i in ids if ans[i]["verdict"] == "ok")
        ab = sum(1 for i in ids if ans[i]["verdict"] == "abstain")
        wr = sum(1 for i in ids if ans[i]["verdict"] == "wrong")
        rq = [hy[i]["rankQ"] for i in ids if i in hy and hy[i]["rankQ"]]
        rh = [hy[i]["rankH"] for i in ids if i in hy and hy[i]["rankH"]]
        n = len(ids)
        print(f"| {k} | {n} | {full}/{n} ({100*full/n:.0f}%) | {legs}/{legs_total} "
              f"({100*legs/max(legs_total,1):.0f}%) | {ok} ({100*ok/n:.0f}%) | {ab} | {wr} | "
              f"{int(st.median(rq)) if rq else '-'} | {int(st.median(rh)) if rh else '-'} |")

    print("\n## 只看 same 与 base 时的差距（『改写难』里有多少是题目重写的问题）\n")
    for k in ("base", "same"):
        ids = set(groups[k])
        full = sum(1 for r in cap if r["iid"] in ids and r["strict"] == 1)
        ok = sum(1 for i in ids if ans[i]["verdict"] == "ok")
        print(f"- {k}: 铺齐 {full}/{len(ids)} ({100*full/len(ids):.0f}%)｜答对 {ok}/{len(ids)} ({100*ok/len(ids):.0f}%)")


if __name__ == "__main__":
    main()
