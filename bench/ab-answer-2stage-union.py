"""两段式的变体：自选不当过滤器，当**加料**。

第一段原样复用 `ab-answer-2stage.json` 里已存的 picked（不再调模型），第二段材料 =
自选行 ∪ 向量块前若干行（去重，按 cap2 截断）。这样即使自选漏了金标，向量块还在兜底。

对照：
  - 单段、全材料、不提引用：77.3% ok+partial（`ab-rejudge-ids-none.json`）
  - 两段、只用自选行：67.4%（`ab-rejudge-2stage.json`）

用法：python bench/ab-answer-2stage-union.py --cap2 1600 --conc 12
"""
import argparse
import importlib.util
import json
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))

_spec = importlib.util.spec_from_file_location("ts", os.path.join(HERE, "ab-answer-2stage.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
ids_mod = ts.ids_mod

SYS_ANS = ts.SYS_ANS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--two-stage", default=os.path.join(HERE, "ab-answer-2stage.json"))
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks-v4.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-answer-2stage-union.json"))
    ap.add_argument("--cap2", type=int, default=1600)
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--no-picks", action="store_true",
                    help="对照：不要自选行，只把向量块截到同一个 cap2（隔离「截断」与「自选」两件事）")
    ap.add_argument("--out-suffix", default="")
    args = ap.parse_args()

    key = ids_mod.load_key()
    prev = json.load(open(args.two_stage, encoding="utf-8"))
    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {f["i"]: f["t"] for f in gen["corpus"]}

    by_text = defaultdict(list)
    for f in gen["corpus"]:
        by_text[ids_mod.norm(f["t"])].append(f["i"])

    def fid_of(line, legs_flat):
        cand = by_text.get(ids_mod.norm(line), [])
        if not cand:
            return None
        for c in cand:
            if c in legs_flat:
                return c
        return min(cand)

    jobs = []
    for r in prev["rows"]:
        cap = r["arm"].split("/")[0]
        name = r["arm"].split("/")[1]
        x = inst[r["iid"]]
        legs_flat = {f for leg in x["legs"] for f in leg}
        order = [(fid_of(l, legs_flat), l) for l in blocks[cap][name][r["iid"]]]
        picked = set(r["picked"])
        if args.no_picks:
            head, rest = [], order
        else:
            head = [p for p in order if p[0] in picked]
            rest = [p for p in order if p[0] not in picked]
        cut, used, seen = [], 0, set()
        for fid, line in head + rest:
            k = ids_mod.norm(line)
            if not k or k in seen:
                continue
            if args.cap2 and used + len(line) + 1 > args.cap2:
                break
            seen.add(k)
            cut.append((fid, line))
            used += len(line) + 1
        jobs.append({"iid": r["iid"], "arm": r["arm"], "x": x, "q": x["q"], "legs": legs_flat,
                     "material": cut, "picked": r["picked"],
                     "selHit": r["selHit"]})
    print(f"{len(jobs)} 个实例｜第二段材料 = 自选 ∪ 向量块，cap2 {args.cap2}", flush=True)

    def stage2(j):
        body = "\n".join(f"- [F{fid}] {line}" if fid else f"- {line}" for fid, line in j["material"])
        user = f"【材料】\n{body}\n\n【问题】{j['q']}\n\n【回答】"
        return ts.call_raw(args.base, key, args.model, SYS_ANS, user, max_tokens=80)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        out = list(ex.map(stage2, jobs))
    print(f"第二段完成｜{time.time()-t0:.0f}s", flush=True)

    rows = []
    for j, (txt, usage) in zip(jobs, out):
        ans, _c = ids_mod.split_answer(txt)
        gold = (qa.get(j["x"]["baseId"], {}).get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for f in sorted(j["legs"]) if f in facts)[:200]
        ans = ids_mod.strip_cites(ans, gold) or ans
        ab = bool(ids_mod.ABSTAIN.search(ans)) or not ans
        rows.append({"arm": j["arm"], "iid": j["iid"], "kind": j["x"]["kind"], "q": j["q"],
                     "gold": gold, "answer": ans, "abstain": ab, "picked": j["picked"],
                     "selHit": j["selHit"], "chars": sum(len(l) + 1 for _f, l in j["material"]),
                     "nLines": len(j["material"]), "usage": usage})
    json.dump({"model": args.model, "rows": rows}, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    c = Counter(r["answer"] == "" for r in rows)
    print(f"写出 {args.out}｜空白答案 {c[True]}｜平均字数 "
          f"{sum(r['chars'] for r in rows)/len(rows):.0f}｜平均行数 "
          f"{sum(r['nLines'] for r in rows)/len(rows):.1f}")


if __name__ == "__main__":
    main()
