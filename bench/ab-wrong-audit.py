"""把 87.2% 那一版里剩下的错答案挖出来，重点看：是不是「旧值/新值选错」。

用法：python bench/ab-wrong-audit.py [--judge ab-rejudge-bg.json] [--answers ab-answer-bg.json]
"""
import argparse
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def load(n):
    return json.load(io.open(os.path.join(HERE, n), encoding="utf-8"))


NUM = re.compile(r"\d+(?:\.\d+)?")
DATE = re.compile(r"2026[年\-/]\d{1,2}[月\-/]\d{1,2}|\d{2}-\d{2}\s+\d{2}:\d{2}")


def nums(s):
    return {m.group(0) for m in NUM.finditer(s or "") if len(m.group(0)) > 1 or "." in m.group(0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", default="ab-rejudge-bg.json")
    ap.add_argument("--answers", default="ab-answer-bg.json")
    ap.add_argument("--out", default="ab-wrong-audit.json")
    args = ap.parse_args()

    judged = load(args.judge)
    raw = {(r["iid"], r["arm"]): r for r in load(args.answers)["rows"]}
    gen = load("ab-gen-v4.json")
    inst = {x["iid"]: x for x in gen["instances"]}
    corpus = [(c["i"], c["t"]) for c in gen["corpus"]]
    qa = {q["id"]: q for q in load(os.path.join("..", "..", "novels", "qa-v2.json"))["questions"]}

    wrong = [r for r in judged if r["llm"] == "wrong"]
    print("错答案 %d 条\n" % len(wrong))
    out = []
    for r in wrong:
        k = (r["iid"], r["arm"])
        rr = raw.get(k, {})
        it = inst.get(r["iid"], {})
        q = qa.get(it.get("baseId"), {})
        ans = rr.get("answer") or ""
        gold = (q.get("answer") or "") or (q.get("evidence") or "")
        an, gn = nums(ans), nums(gold)
        # 答案里的数字出现在语料哪些事实里
        hit_ans, hit_gold = [], []
        for fid, t in corpus:
            if an and an & nums(t):
                hit_ans.append((fid, t))
            if gn and gn & nums(t):
                hit_gold.append((fid, t))
        rec = {
            "iid": r["iid"], "type": it.get("type"), "kind": it.get("kind"),
            "q": it.get("q"), "answer": ans, "gold": gold,
            "evidence": q.get("evidence") or "", "anchors": q.get("anchors") or [],
            "ansNums": sorted(an), "goldNums": sorted(gn),
            "ansFacts": [{"id": f, "t": t} for f, t in hit_ans[:3]],
            "goldFacts": [{"id": f, "t": t} for f, t in hit_gold[:3]],
        }
        out.append(rec)

        print("[%s] %s / %s" % (rec["iid"], rec["type"], rec["kind"]))
        print("   Q   :", rec["q"])
        print("   答  :", ans, "  数字", rec["ansNums"])
        print("   金标:", gold[:80], "  数字", rec["goldNums"], "| 出处", rec["evidence"][:40])
        for f in rec["ansFacts"]:
            print("   答数字出处 F%s: %s" % (f["id"], f["t"][:110]))
        for f in rec["goldFacts"]:
            print("   金标数字出处 F%s: %s" % (f["id"], f["t"][:110]))
        if rec["ansFacts"] and rec["goldFacts"]:
            same = {f["id"] for f in rec["ansFacts"]} & {f["id"] for f in rec["goldFacts"]}
            rec["conflict"] = "same-fact" if same else "different-facts"
            print("   → 判定:", rec["conflict"])
        print()

    json.dump(out, io.open(os.path.join(HERE, args.out), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("写出", args.out)


if __name__ == "__main__":
    main()
