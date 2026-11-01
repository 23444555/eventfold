"""抽查基线（4800/vec、材料全给、不提引用）里那 27 条沉默到底是不是「材料里有」。

用法：python bench/ab-abstain-audit.py [--cap 4800] [--arm vec]
输出 bench/ab-abstain-audit.json + 屏幕预览。
"""
import argparse
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", default="ab-answer-ids-none.json")
    ap.add_argument("--judge", default="ab-rejudge-ids-none.json")
    ap.add_argument("--cap", default="4800")
    ap.add_argument("--arm", default="4800/vec", help="结果行里的臂名，形如 4800/vec")
    ap.add_argument("--pq-arm", default=None, help="ab-cap 里 perQuery 的臂名，默认取 --arm 斜杠后那段")
    ap.add_argument("--out", default="ab-abstain-audit.json")
    ap.add_argument("--preview", type=int, default=0)
    args = ap.parse_args()

    rows = load(args.answers)["rows"]
    judged = {}
    for r in load(args.judge):
        judged[(r.get("iid"), r.get("arm"))] = r
    cap = load("ab-cap-v4.json")
    gen = load("ab-gen-v4.json")
    inst = {x["iid"]: x for x in gen["instances"]}
    corpus = {c["i"]: c["t"] for c in gen["corpus"]}
    qa = load(os.path.join("..", "..", "novels", "qa-v2.json"))["questions"]
    by_id = {q["id"]: q for q in qa}

    per = {}
    pq_arm = args.pq_arm or args.arm.split("/")[-1]
    for entry in cap["perQuery"].get(args.cap, {}).get(pq_arm, []):
        per[entry["iid"]] = entry

    abst = [r for r in rows if r.get("arm") == args.arm
            and (judged.get((r.get("iid"), r.get("arm"))) or {}).get("llm") == "abstain"]

    out = []
    for r in abst:
        iid = r["iid"]
        it = inst.get(iid, {})
        pq = per.get(iid, {})
        base = it.get("baseId") or iid
        src = by_id.get(base, {})
        legs = it.get("legs") or []
        missing = pq.get("missing") or []
        miss_facts = []
        for idx in missing:
            if idx < len(legs):
                for fid in legs[idx]:
                    if fid in corpus:
                        miss_facts.append(corpus[fid])
                        break
        out.append({
            "iid": iid, "baseId": base, "kind": it.get("kind"), "qtype": it.get("type"),
            "q": it.get("q"), "modelAnswer": r.get("answer"),
            "goldAnswer": src.get("answer") or "",
            "evidence": src.get("evidence") or "",
            "anchors": src.get("anchors") or [],
            "legsTotal": pq.get("legs"), "strict": pq.get("strict"),
            "full": pq.get("strict") == pq.get("legs"),
            "missingCount": len(missing), "missingFacts": miss_facts,
            "chars": pq.get("chars"),
        })

    full = [o for o in out if o["full"]]
    partial = [o for o in out if not o["full"]]
    print("沉默 %d 条：材料全铺齐 %d，缺腿 %d" % (len(out), len(full), len(partial)))
    from collections import Counter
    print("按题型：", dict(Counter(o["qtype"] for o in out)))
    print("全铺齐的题型：", dict(Counter(o["qtype"] for o in full)))
    print("缺腿的题型：", dict(Counter(o["qtype"] for o in partial)))
    print("原问/改写：", dict(Counter(o["kind"] for o in out)))
    with io.open(os.path.join(HERE, args.out), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", args.out)
    show = full if args.preview else []
    for o in show[:max(args.preview, 0)]:
        print("-")
        print(" [%s] %s (%s) 缺%d腿 strict=%s" % (o["iid"], o["qtype"], o["kind"],
                                                o["missingCount"], o["strict"]))
        print("   Q:", o["q"])
        print("   A:", o["modelAnswer"])
        print("   G:", (o["goldAnswer"] or o["evidence"])[:100])
        if o["missingFacts"]:
            print("   MISS:", " | ".join(x[:70] for x in o["missingFacts"][:2]))


if __name__ == "__main__":
    main()
