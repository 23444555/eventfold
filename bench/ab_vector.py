"""向量臂 A/B：同一批题，用 bge-m3 取候选，可再叠交叉编码器重排，填到跟档案块同样的字数。

这是 ab-coverage.mjs 里那条「字面召回」基线的硬版本——真正的问题是
「一个现代的向量库 + 重排器，在同样的 token 预算下能不能追平这层」。

判分口径与 ab-coverage.mjs 完全一致（同样的 norm / bigram / J≥0.55 近似）。

用法：
  E:\\dshxz\\hindsight\\.venv\\Scripts\\python.exe bench\\ab_vector.py \
      --qa novels/qa-v2.json --req novels/required-sets.json \
      --archive p0-archive/archive.json --budget-from bench/ab-result.json \
      --model E:\\dshxz\\hindsight\\models\\bge-m3-ms \
      --rerank E:\\dshxz\\hindsight\\models\\bge-reranker-base
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def norm(s):
    s = str(s or "")
    if " | When:" in s:
        s = s.split(" | When:")[0]
    return s.strip()


def bigrams(s):
    z = "".join(ch for ch in norm(s) if ch not in " \t\n，。、；：")
    return {z[i : i + 2] for i in range(len(z) - 1)}


def jac(a, b):
    if not a and not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter or 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa", default=str(HERE.parent.parent / "novels" / "qa-v2.json"))
    ap.add_argument("--req", default=str(HERE.parent.parent / "novels" / "required-sets.json"))
    ap.add_argument("--archive", default=str(HERE.parent.parent / "p0-archive" / "archive.json"))
    ap.add_argument("--budget-from", default=str(HERE / "ab-result.json"))
    ap.add_argument("--model", default=r"E:\dshxz\hindsight\models\bge-m3-ms")
    ap.add_argument("--rerank", default=r"E:\dshxz\hindsight\models\bge-reranker-base")
    ap.add_argument("--topk", type=int, default=50)
    ap.add_argument("--near", type=float, default=0.55)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（调试用）")
    args = ap.parse_args()

    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    qa = json.loads(Path(args.qa).read_text(encoding="utf-8"))["questions"]
    req = json.loads(Path(args.req).read_text(encoding="utf-8"))
    data = json.loads(Path(args.archive).read_text(encoding="utf-8"))
    budgets = json.loads(Path(args.budget_from).read_text(encoding="utf-8"))

    ep_rows = {r["qid"]: r for r in budgets["arms"]["episode"]["rows"]}
    ep_chars = {qid: r["chars"] for qid, r in ep_rows.items()}

    facts = []
    for e in data["events"]:
        for f in e["facts"]:
            facts.append((f["i"], f.get("d", ""), norm(f["t"])))
    text_of = {i: t for i, _, t in facts}
    docs = [f"[{d}] {t}" for _, d, t in facts]
    qmap = {q["id"]: q for q in qa}

    qids = [q for q, spec in req.items() if isinstance(spec, dict) and spec.get("legs")]
    qids = [q for q in qids if q in qmap]
    if args.limit:
        qids = qids[: args.limit]
    print(f"facts {len(facts)} | 题 {len(qids)}", flush=True)

    dev = args.device if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(args.model, device=dev)
    model.half()
    model.max_seq_length = 512
    emb = model.encode(docs, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype=np.float32)

    reranker = None
    if args.rerank and Path(args.rerank).exists():
        reranker = CrossEncoder(args.rerank, device=dev)

    def score_one(qid, picked):
        """picked = 铺进块里的事实正文（已归一化，无日期前缀）"""
        pg = [bigrams(t) for t in picked]
        spec = req[qid]
        strict = approx = 0
        for leg in spec["legs"]:
            exact = any(norm(text_of.get(i, "")) in picked for i in leg)
            near = exact or any(
                jac(bigrams(text_of.get(i, "")), p) >= args.near for i in leg for p in pg
            )
            strict += 1 if exact else 0
            approx += 1 if near else 0
        return {"strict": strict, "approx": approx, "legs": len(spec["legs"])}

    def fill(order, budget):
        picked, used = [], 0
        for idx in order:
            if used + len(docs[idx]) + 1 > budget:
                continue
            picked.append(facts[idx][2])
            used += len(docs[idx]) + 1
        return picked, used

    arms = {"vec": {}, "vec+rerank": {}}
    for n, qid in enumerate(qids, 1):
        q = qmap[qid]["q"]
        budget = ep_chars.get(qid, 4684)
        qv = np.asarray(
            model.encode([q], normalize_embeddings=True, show_progress_bar=False), dtype=np.float32
        )
        sims = (emb @ qv.T).ravel()
        top = np.argsort(-sims)[: max(args.topk, 10)]

        picked, used = fill(top, budget)
        arms["vec"][qid] = {**score_one(qid, picked), "chars": used, "facts": len(picked)}

        if reranker is not None:
            pairs = [(q, docs[idx]) for idx in top]
            rs = reranker.predict(pairs, batch_size=32, show_progress_bar=False)
            order = [top[k] for k in np.argsort(-np.asarray(rs))]
            picked2, used2 = fill(order, budget)
            arms["vec+rerank"][qid] = {
                **score_one(qid, picked2),
                "chars": used2,
                "facts": len(picked2),
            }
        if n % 5 == 0:
            print(f"  {n}/{len(qids)}", flush=True)

    def summarize(name):
        rows = arms[name].values()
        n_all = len(arms[name])
        full = sum(1 for r in rows if r["strict"] == r["legs"])
        near = sum(1 for r in rows if r["approx"] == r["legs"])
        legs = sum(r["strict"] for r in arms[name].values())
        tot = sum(r["legs"] for r in arms[name].values())
        chars = sum(r["chars"] for r in arms[name].values()) / max(n_all, 1)
        cnt = sum(r["facts"] for r in arms[name].values()) / max(n_all, 1)
        return n_all, full, near, legs, tot, chars, cnt

    print("\n| 方案 | 全铺齐（严格） | 全铺齐（近似） | 腿覆盖 | 平均字数 | 平均条数 |")
    print("|---|---|---|---|---|---|")
    for name in arms:
        n_all, full, near, legs, tot, chars, cnt = summarize(name)
        print(
            f"| {name} | {full}/{n_all} ({100*full/max(n_all,1):.1f}%) | "
            f"{near}/{n_all} ({100*near/max(n_all,1):.1f}%) | {legs}/{tot} | "
            f"{chars:.0f} | {cnt:.1f} |"
        )
    b_ep, b_fb = budgets["arms"]["episode"], budgets["arms"]["flatBudget"]
    print(f"| （对照）档案层 | {b_ep['full']}/{n} | {b_ep['near']}/{n} | — | {b_ep['avgChars']} | — |")
    print(
        f"| （对照）等预算字面召回 | {b_fb['full']}/{n} | {b_fb['near']}/{n} | — | {b_fb['avgChars']} | — |"
    )

    print("\n逐题（严格口径）：")
    for qid in qids:
        ep = ep_rows.get(qid, {})
        v = arms["vec"][qid]
        line = f"{qid:8} 档案 {ep.get('strict','-')}/{ep.get('legs','-')}   vec {v['strict']}/{v['legs']}"
        if reranker is not None:
            r = arms["vec+rerank"][qid]
            line += f"   vec+rr {r['strict']}/{r['legs']}"
        print(line)

    if args.out:
        Path(args.out).write_text(
            json.dumps({"n": n, "near": args.near, "arms": arms}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
