"""把「原本沉默的 27 条」在 bestguess 下的答案与金标并排，人工核对判分是否过宽。"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(n):
    return json.load(io.open(os.path.join(HERE, n), encoding="utf-8"))


qa = {q["id"]: q for q in load(os.path.join("..", "..", "novels", "qa-v2.json"))["questions"]}
gen = load("ab-gen-v4.json")
inst = {x["iid"]: x for x in gen["instances"]}
facts = {f["i"]: f["t"] for f in gen["corpus"]}

before = {(r["iid"], r["arm"]): r for r in load("ab-rejudge-ids-none.json")}
after = {(r["iid"], r["arm"]): r for r in load("ab-rejudge-bg.json")}
raw = {(r["iid"], r["arm"]): r for r in load("ab-answer-bg.json")["rows"]}

rows = [r for r in load("ab-rejudge-ids-none.json") if r["llm"] == "abstain"]
print("共 %d 条原本沉默\n" % len(rows))
for r in rows:
    k = (r["iid"], r["arm"])
    a = after.get(k, {})
    rr = raw.get(k, {})
    it = inst.get(r["iid"], {})
    q = qa.get(it.get("baseId"), {})
    legs = [f for leg in it.get("legs", []) for f in leg]
    leg_txt = " || ".join(facts[f][:90] for f in legs[:2] if f in facts)
    print("[%s] %s" % (r["iid"], it.get("type")))
    print("  Q   : %s" % it.get("q"))
    print("  答  : %s   → %s" % (rr.get("answer"), a.get("llm")))
    print("  金标: ans=%r evid=%r" % ((q.get("answer") or "")[:60], (q.get("evidence") or "")[:90]))
    print("  证据: %s" % leg_txt)
