"""多臂压测：同一批实例、同一份字数预算、同一套机械判分。

臂（arm）：
  nil            空上下文（地板）
  flat8          8 条词面覆盖最高的事实（多数记忆系统的返回形态）
  flatBudget     8 条起继续往下填，直到花完档案块的字数预算
  bm25           字符 bigram BM25 检索，同预算填充
  vec            bge-m3 余弦 top-k，同预算填充
  vecRr          bge-m3 top-50 -> bge-reranker-base 重排，同预算填充
  episode        档案块（archiveBlock）+ 8 条事实   <- 生产形态
  episodeNoBoost 同上但关掉复杂度加预算（ablation）
  episodePlusVec 档案块与向量臂的并集（不裁剪，字数翻倍，用来量上限）

判分：每条腿是一组等价事实 id，块里出现任一即算齐；strict=归一化文本完全包含，
near=字符 bigram Jaccard >= 0.55。

用法：
  python bench/ab-arms.py --gen bench/ab-gen.json --out bench/ab-result-v2.json
"""
import argparse
import json
import math
import os
import re
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NEAR = 0.55
MODEL = os.environ.get("AB_EMBED_MODEL", r"E:\dshxz\hindsight\models\bge-m3-ms")
RERANK = os.environ.get("AB_RERANK_MODEL", r"E:\dshxz\hindsight\models\bge-reranker-base")
ARMS = ["nil", "flat8", "flatBudget", "bm25", "vec", "vecRr", "episodeNoBoost", "episode", "episodePlusVec"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} ")


def norm(s):
    return re.sub(r"\s*\|\s*When:.*$", "", str(s or "")).strip()


def bigrams(s):
    z = re.sub(r"[\s，。、；：]", "", norm(s))
    return set(z[i:i + 2] for i in range(len(z) - 1))


def jac(a, b):
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter or 1)


class BM25:
    """字符 bigram 版 BM25，够用且不需要分词。"""

    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [bigrams(d) for d in docs]
        self.lens = [len(d) for d in self.docs]
        self.avg = sum(self.lens) / max(1, len(self.docs))
        self.df = Counter()
        for d in self.docs:
            self.df.update(set(d))
        self.n = len(self.docs)
        self.post = {}
        for i, d in enumerate(self.docs):
            for g in d:
                self.post.setdefault(g, []).append(i)

    def score(self, q):
        out = np.zeros(self.n)
        for g in bigrams(q):
            df = self.df.get(g, 0)
            if not df:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for i in self.post.get(g, ()):
                tf = 1.0
                out[i] += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
        return out


def score(legs, picked, text_of, near=NEAR):
    pg = [bigrams(p) for p in picked]
    pset = set(picked)
    strict = near_hit = 0
    missing = []
    for leg in legs:
        ok = any(norm(text_of.get(i, "")) in pset for i in leg)
        if ok:
            strict += 1
            near_hit += 1
            continue
        got = False
        for i in leg:
            g = bigrams(text_of.get(i, ""))
            if g and any(jac(g, p) >= near for p in pg):
                got = True
                break
        if got:
            near_hit += 1
        else:
            missing.append(leg[0])
    return strict, near_hit, missing


def fill(ranked, budget, corpus_by_i):
    keep, used = [], 0
    for i, _s in ranked:
        f = corpus_by_i.get(i)
        if not f:
            continue
        line = f"{f['d']} {f['t']}"
        if used + len(line) + 1 > budget:
            continue
        keep.append(line)
        used += len(line) + 1
        if used >= budget * 0.99:
            break
    return keep


def binom_two_sided(k, n, p=0.5):
    if n == 0:
        return 1.0
    tot = sum(math.comb(n, i) for i in range(n + 1))
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) if k <= n / 2 else \
        sum(math.comb(n, i) for i in range(k, n + 1))
    return min(1.0, 2 * tail / tot)


def agg(rows):
    full = sum(1 for r in rows if r["strict"] == r["legs"])
    near = sum(1 for r in rows if r["near"] == r["legs"])
    return {
        "n": len(rows), "full": full, "nearFull": near,
        "legs": sum(r["strict"] for r in rows),
        "legsTotal": sum(r["legs"] for r in rows),
        "chars": round(sum(r["chars"] for r in rows) / max(1, len(rows))),
        "facts": round(sum(r["facts"] for r in rows) / max(1, len(rows)), 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-result-v2.json"))
    ap.add_argument("--topk", type=int, default=50)
    ap.add_argument("--device", default="")
    args = ap.parse_args()

    d = json.load(open(args.gen, encoding="utf-8"))
    inst, corpus = d["instances"], d["corpus"]
    corpus_by_i = {f["i"]: f for f in corpus}
    text_of = {f["i"]: f["t"] for f in corpus}
    print(f"实例 {len(inst)}｜事实 {len(corpus)}｜预算中位 {int(np.median([x['epChars'] for x in inst]))} 字", flush=True)

    import torch
    from sentence_transformers import SentenceTransformer, CrossEncoder
    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device {dev}｜embed {MODEL}", flush=True)
    st = SentenceTransformer(MODEL, device=dev)
    st.max_seq_length = 512
    if dev == "cuda":
        st.half()
    C = st.encode([f["t"] for f in corpus], batch_size=128, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False)
    Q = st.encode([x["q"] for x in inst], batch_size=32, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False)
    sims = (Q @ C.T).astype(np.float32)
    top_idx = np.argsort(-sims, axis=1)[:, : args.topk]
    print(f"余弦矩阵 {sims.shape}", flush=True)

    reranker = None
    if os.path.isdir(RERANK):
        print(f"rerank {RERANK}", flush=True)
        reranker = CrossEncoder(RERANK, device=dev)

    bm = BM25([f["t"] for f in corpus])

    arms = {k: [] for k in ARMS}
    for k, x in enumerate(inst):
        budget = x["epChars"]
        ep = [norm(t) for t in x["episode"]] + [norm(t) for t in x["flat8"]]
        picked = {
            "nil": [],
            "flat8": [norm(t) for t in x["flat8"]],
            "flatBudget": [re.sub(DATE_RE, "", t) for t in x["flatBudget"]],
            "episode": ep,
            "episodeNoBoost": [norm(t) for t in x["noBoost"]] + [norm(t) for t in x["flat8"]],
        }

        bs = bm.score(x["q"])
        order = [(corpus[i]["i"], float(bs[i])) for i in np.argsort(-bs)[:400] if bs[i] > 0]
        picked["bm25"] = [re.sub(DATE_RE, "", l) for l in fill(order, budget, corpus_by_i)]

        vsim = [(corpus[i]["i"], float(sims[k][i])) for i in top_idx[k]]
        picked["vec"] = [re.sub(DATE_RE, "", l) for l in fill(vsim, budget, corpus_by_i)]

        if reranker is not None:
            pairs = [(x["q"], corpus_by_i[i]["t"]) for i, _ in vsim]
            sc = reranker.predict(pairs, batch_size=32, show_progress_bar=False)
            rr = sorted(zip([i for i, _ in vsim], [float(v) for v in sc]), key=lambda t: -t[1])
            picked["vecRr"] = [re.sub(DATE_RE, "", l) for l in fill(rr, budget, corpus_by_i)]
        else:
            picked["vecRr"] = picked["vec"]

        for name, lines in picked.items():
            s, nr, missing = score(x["legs"], lines, text_of)
            arms[name].append({
                "iid": x["iid"], "baseId": x["baseId"], "kind": x["kind"],
                "legs": len(x["legs"]), "strict": s, "near": nr,
                "chars": sum(len(l) + 1 for l in lines), "facts": len(lines), "missing": missing,
            })

        # 并集臂：档案块 + 向量臂，两份都进上下文（不裁剪 → 字数翻倍，用来看上限）
        union, seen = [], set()
        for line in picked["episode"] + picked["vec"]:
            key = re.sub(DATE_RE, "", line)
            if key in seen:
                continue
            seen.add(key)
            union.append(line)
        s, nr, missing = score(x["legs"], union, text_of)
        arms["episodePlusVec"].append({
            "iid": x["iid"], "baseId": x["baseId"], "kind": x["kind"],
            "legs": len(x["legs"]), "strict": s, "near": nr,
            "chars": sum(len(l) + 1 for l in union), "facts": len(union), "missing": missing,
        })

        if (k + 1) % 20 == 0:
            print(f"  {k + 1}/{len(inst)}", flush=True)

    n = len(inst)
    total_legs = sum(len(x["legs"]) for x in inst)
    summary = {name: {"all": agg(arms[name]),
                      "base": agg([r for r in arms[name] if r["kind"] == "base"]),
                      "variant": agg([r for r in arms[name] if r["kind"] == "variant"])}
               for name in ARMS}

    ep = {r["iid"]: r for r in arms["episode"]}
    sig = {}
    for name in ARMS:
        if name == "episode":
            continue
        a = {r["iid"]: r for r in arms[name]}
        win = sum(1 for i in ep if ep[i]["strict"] == ep[i]["legs"] and a[i]["strict"] < a[i]["legs"])
        lose = sum(1 for i in ep if ep[i]["strict"] < ep[i]["legs"] and a[i]["strict"] == a[i]["legs"])
        p_q = binom_two_sided(min(win, lose), win + lose)
        only_ep = sum(max(0, ep[i]["strict"] - a[i]["strict"]) for i in ep)
        only_a = sum(max(0, a[i]["strict"] - ep[i]["strict"]) for i in ep)
        sig[name] = {"queriesWin": win, "queriesLose": lose, "pQuery": round(p_q, 5),
                     "legsOnlyEpisode": only_ep, "legsOnlyArm": only_a,
                     "pLeg": round(binom_two_sided(min(only_ep, only_a), only_ep + only_a), 6)}

    res = {"instances": n, "totalLegs": total_legs, "near": NEAR, "lat": d.get("lat"),
           "arms": ARMS, "summary": summary, "significance": sig,
           "perQuery": {k: arms[k] for k in ARMS}}
    json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n## 全量（{n} 实例 / {total_legs} 腿次）\n")
    print("| 方案 | 全铺齐 严格 | 全铺齐 近似 | 腿齐 | 平均字数 | 平均条数 | ≈token |")
    print("|---|---|---|---|---|---|---|")
    for name in ARMS:
        s = summary[name]["all"]
        print(f"| {name} | {s['full']}/{n} ({100*s['full']/n:.1f}%) | {s['nearFull']}/{n} ({100*s['nearFull']/n:.1f}%) "
              f"| {s['legs']}/{s['legsTotal']} ({100*s['legs']/s['legsTotal']:.1f}%) | {s['chars']} | {s['facts']} | {int(s['chars']*0.65)} |")

    print("\n## 原问 vs 改写（严格口径全铺齐）\n")
    print("| 方案 | 原问 44 | 改写 68 | 掉多少 |")
    print("|---|---|---|---|")
    for name in ARMS:
        b, v = summary[name]["base"], summary[name]["variant"]
        print(f"| {name} | {b['full']}/{b['n']} ({100*b['full']/b['n']:.1f}%) | {v['full']}/{v['n']} ({100*v['full']/v['n']:.1f}%) "
              f"| {100*b['full']/b['n'] - 100*v['full']/v['n']:+.1f} pt |")

    print("\n## 显著性（episode 为对照，逐实例符号检验 / 逐腿 McNemar）\n")
    print("| 对比 | 题上赢 | 题上输 | p(题) | 腿上只档案有 | 腿上只对面有 | p(腿) |")
    print("|---|---|---|---|---|---|---|")
    for name in ARMS:
        if name == "episode":
            continue
        s = sig[name]
        print(f"| episode vs {name} | {s['queriesWin']} | {s['queriesLose']} | {s['pQuery']} "
              f"| {s['legsOnlyEpisode']} | {s['legsOnlyArm']} | {s['pLeg']} |")

    print(f"\n写出 {args.out}")
    print("\n### 缺得最多的腿（前 8）")
    for name in ARMS:
        if name == "nil":
            continue
        c = Counter()
        for r in arms[name]:
            c.update(r["missing"])
        print(f"  {name}: " + ", ".join(f"{i}x{k}" for i, k in c.most_common(8)))


if __name__ == "__main__":
    main()
