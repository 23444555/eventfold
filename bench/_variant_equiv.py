"""改写题是不是「还在问同一件事」？——用模型逐条判，只用 68 次调用。

背景：`novels/variants-3-src.json` 的变体是「抽掉人名与领域词」的重写，但抽样看下来，
有几条改写其实**换了问题**（例：原问「502 持续多久」，改写问「谁去跑的、啥时候弄好」），
而判分仍用原题的金标证据集 → 这类题系统怎么检索都「错」。

这里让模型只做一件事：给原问、原答、改写问，判改写是否仍在问同一件事。
输出 same / partial / different，并给一句理由。

用法：python bench/_variant_equiv.py --conc 8
"""
import argparse
import json
import os
import re
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(HERE))
ENV = r"E:\dshxz\hindsight\.env"

SYS = ("你在校验一个改写数据集。给你原问、原答、改写问。判断改写问是否仍在问同一件事——"
       "即：原答能不能直接回答改写问？只输出一行 JSON："
       '{"verdict":"same|partial|different","why":"不超过20字"}')


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, user, timeout=60):
    body = {"model": model,
            "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
            "max_tokens": 80, "temperature": 0, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            return (d["choices"][0]["message"].get("content") or "").strip(), d.get("usage") or {}
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return "", {}


def parse(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return "unknown", (txt or "")[:40]
    try:
        d = json.loads(m.group(0))
        v = str(d.get("verdict", "")).lower()
        v = v if v in ("same", "partial", "different") else "unknown"
        return v, str(d.get("why", ""))[:40]
    except Exception:
        return "unknown", (txt or "")[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(HERE, "_variant-equiv.json"))
    args = ap.parse_args()

    gen = json.load(open(os.path.join(HERE, "ab-gen.json"), encoding="utf-8"))
    qa = json.load(open(os.path.join(WORKSPACE, "novels", "qa-v2.json"), encoding="utf-8"))
    qmap = {q["id"]: q for q in qa["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {c["i"]: c["t"] for c in gen["corpus"]}

    jobs = []
    for x in gen["instances"]:
        if x["kind"] != "variant":
            continue
        base = inst.get(x["baseId"], {})
        q = qmap.get(x["baseId"], {})
        gold = (q.get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for leg in base.get("legs", []) for f in leg[:1] if f in facts)[:160]
        user = f"【原问】{base.get('q','')}\n【原答】{gold}\n【改写问】{x['q']}"
        jobs.append((x["iid"], x["baseId"], user, x["q"]))

    key = load_key()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        res = list(ex.map(lambda j: call(args.base, key, args.model, j[2]), jobs))
    rows = []
    for (iid, bid, _u, vq), (txt, usage) in zip(jobs, res):
        v, why = parse(txt)
        rows.append({"iid": iid, "baseId": bid, "variant": vq, "verdict": v, "why": why})
    c = Counter(r["verdict"] for r in rows)
    print(f"{len(rows)} 条改写，{time.time()-t0:.0f}s\n")
    for k in ("same", "partial", "different", "unknown"):
        if c[k]:
            print(f"- {k}: {c[k]}（{100*c[k]/len(rows):.0f}%）")
    print("\n## different 的样本（这些题被判分当同一题）\n")
    for r in rows:
        if r["verdict"] == "different":
            print(f"**{r['iid']}** ← {r['baseId']}")
            print(f"- 改写：{r['variant']}")
            print(f"- 理由：{r['why']}\n")
    json.dump(rows, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"写出 {args.out}")


if __name__ == "__main__":
    main()
