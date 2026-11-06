"""HyDE 式查询扩展的离线验证：先让模型编一句「像原文的假答案」，再拿它去检索。

问题在于改写题（抽掉了人名与术语），向量和 BM25 都没有词面钩子。这轮只测一件事，
不碰线上路径：**假答案向量把金标的名次往前挪了多少？**

三档对照，同一个 1,067 条事实库、同一批 112 个实例：
  q        问题原文向量
  hypo     假答案向量
  rrf      q 与 hypo 的 RRF 融合

指标：金标里最好那条事实的名次（1 起），给中位、p25/p75、top-5/20/50 命中率。

用法：
  python bench/ab-hyde.py --limit 8            # 冒烟
  python bench/ab-hyde.py --conc 8 --out bench/ab-hyde.json
"""
import argparse
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))
ENV = r"E:\dshxz\hindsight\.env"
MODEL = r"E:\dshxz\hindsight\models\bge-m3-ms"

SYS = (
    "你在帮记忆检索系统做查询扩展：把问题改写成一句「可能出现在记忆原文里的陈述句」。"
    "保留问题里的时间、对象、数字线索，缺的就编一个最合理的，不要反问、不要解释。"
    "只输出这一句，25 字以内。"
)


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, q, timeout=60):
    body = {"model": model,
            "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": q}],
            "max_tokens": 64, "temperature": 0.3, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            return (d["choices"][0]["message"].get("content") or "").strip(), d.get("usage") or {}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(1.5 * (attempt + 1))
    return "", {"error": last}


def clean(s):
    s = re.sub(r"^[\s\"'「『]|[\s\"'」』]$", "", s or "")
    return re.sub(r"\s+", "", s)[:60]


def ranks(sims, legs, ids):
    """每条腿取最好名次，再取最小。返回 (rank, is_hit_within_legs)。"""
    order = np.argsort(-sims)
    pos = {int(ids[order[j]]): j + 1 for j in range(len(order))}
    best = None
    for leg in legs:
        rs = [pos[f] for f in leg if f in pos]
        if rs:
            r = min(rs)
            best = r if best is None else min(best, r)
    return best


def stats_seq(vals, tag):
    a = np.array([v for v in vals if v])
    if not len(a):
        return f"{tag}: 无名次"
    return (f"{tag}: 中位 {int(np.median(a))}｜p25 {int(np.percentile(a,25))}｜p75 {int(np.percentile(a,75))}"
            f"｜top5 {100*(a<=5).mean():.1f}%｜top20 {100*(a<=20).mean():.1f}%｜top50 {100*(a<=50).mean():.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-hyde.json"))
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="")
    args = ap.parse_args()

    key = load_key()
    d = json.load(open(args.gen, encoding="utf-8"))
    inst, corpus = d["instances"], d["corpus"]
    if args.limit:
        inst = inst[: args.limit]
    ids = [c["i"] for c in corpus]

    print(f"实例 {len(inst)}｜事实 {len(corpus)}｜模型 {args.model}", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        gen = list(ex.map(lambda x: call(args.base, key, args.model, x["q"]), inst))
    hypos = [clean(g[0]) for g in gen]
    errs = sum(1 for h in hypos if not h)
    tin = sum((g[1] or {}).get("prompt_tokens", 0) for g in gen)
    tout = sum((g[1] or {}).get("completion_tokens", 0) for g in gen)
    print(f"假答案 {len(hypos)} 条（空 {errs}）｜{time.time()-t0:.0f}s｜token 入 {tin} / 出 {tout}", flush=True)
    print("样例：" + " / ".join(h for h in hypos[:4] if h), flush=True)

    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    st = SentenceTransformer(MODEL, device=dev)
    st.max_seq_length = 512
    if dev == "cuda":
        st.half()
    C = st.encode([c["t"] for c in corpus], batch_size=128, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
    Q = st.encode([x["q"] for x in inst], batch_size=32, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
    H = st.encode([h or x["q"] for h, x in zip(hypos, inst)], batch_size=32, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False).astype(np.float32)

    rq, rh, rr = [], [], []
    rrf_k = 60
    rows = []
    for k, x in enumerate(inst):
        sq, sh = Q[k] @ C.T, H[k] @ C.T
        oq, oh = np.argsort(-sq), np.argsort(-sh)
        pq = {int(ids[oq[j]]): j + 1 for j in range(len(oq))}
        ph = {int(ids[oh[j]]): j + 1 for j in range(len(oh))}
        fused = sorted(pq, key=lambda f: -(1 / (rrf_k + pq[f]) + 1 / (rrf_k + ph[f])))
        pf = {f: j + 1 for j, f in enumerate(fused)}
        r1 = ranks(sq, x["legs"], ids)
        r2 = ranks(sh, x["legs"], ids)
        # 融合名次直接在排序好的顺序上算
        best = None
        for leg in x["legs"]:
            rs = [pf[f] for f in leg if f in pf]
            if rs:
                best = min(rs) if best is None else min(best, min(rs))
        rq.append(r1)
        rh.append(r2)
        rr.append(best)
        rows.append({"iid": x["iid"], "kind": x["kind"], "q": x["q"], "hypo": hypos[k],
                     "rankQ": r1, "rankH": r2, "rankRRF": best})

    print("\n## 金标名次（全库 1,067 条，越小越好）\n")
    for vals, tag in ((rq, "q"), (rh, "hypo"), (rr, "rrf(q,hypo)")):
        print(stats_seq(vals, tag))

    base = [r for r in rows if r["kind"] == "base"]
    var = [r for r in rows if r["kind"] == "variant"]
    print("\n## 原问 vs 改写（中位名次）\n")
    for tag, grp in (("原问", base), ("改写", var)):
        for key_ in ("rankQ", "rankH", "rankRRF"):
            a = np.array([r[key_] for r in grp if r[key_]])
            if len(a):
                print(f"- {tag} {key_}: 中位 {int(np.median(a))}｜top20 {100*(a<=20).mean():.0f}%")

    better = sum(1 for r in rows if r["rankH"] and r["rankQ"] and r["rankH"] < r["rankQ"])
    worse = sum(1 for r in rows if r["rankH"] and r["rankQ"] and r["rankH"] > r["rankQ"])
    print(f"\nhypo 比 q 好 {better} 个、差 {worse} 个（其余持平/无名次）")

    json.dump({"instances": len(inst), "model": args.model, "tokensIn": tin, "tokensOut": tout,
               "rows": rows}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"写出 {args.out}")


if __name__ == "__main__":
    main()
