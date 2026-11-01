"""缺的腿，是「检索排不到」还是「块里被挤掉」？

对每个实例，用 bge-m3 把问题编码，在 1,067 条事实上算余弦，取每条腿里最好那条事实的名次。
然后把名次分布和「4800/vec 臂里这条腿在不在块里」对起来：
  - 名次靠前（<=20）却不在块里 → 是拼块的规则（去重/预算/截断）在丢东西
  - 名次很靠后（>200）→ 是检索本身够不到，渲染层再改也没用
"""
import json
import os

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = r"E:\dshxz\hindsight\models\bge-m3-ms"


def main():
    gen = json.load(open(os.path.join(HERE, "ab-gen.json"), encoding="utf-8"))
    cap = json.load(open(os.path.join(HERE, "ab-cap-result.json"), encoding="utf-8"))
    blocks = json.load(open(os.path.join(HERE, "ab-cap-blocks.json"), encoding="utf-8"))

    corpus = gen["corpus"]
    texts = [c["t"] for c in corpus]
    fid = [c["i"] for c in corpus]
    txt_of = {c["i"]: str(c["t"]) for c in corpus}

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = SentenceTransformer(MODEL, device=dev)
    m.max_seq_length = 512
    m.half()

    C = m.encode(texts, batch_size=64, normalize_embeddings=True,
                 convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
    qs = [x["q"] for x in gen["instances"]]
    Q = m.encode(qs, batch_size=32, normalize_embeddings=True,
                 convert_to_numpy=True, show_progress_bar=False).astype(np.float32)

    sims = Q @ C.T
    order = np.argsort(-sims, axis=1)

    # 4800/vec 每条腿在不在块里
    cov = {r["iid"]: r for r in cap["perQuery"]["4800"]["vec"]}
    blk = blocks["4800"]["vec"]

    buckets = [("1-5", 1, 5), ("6-20", 6, 20), ("21-50", 21, 50),
               ("51-200", 51, 200), (">200", 201, 10 ** 9)]
    stat = {b[0]: {"n": 0, "inBlock": 0} for b in buckets}
    ranks = []

    for k, x in enumerate(gen["instances"]):
        row = cov[x["iid"]]
        if row["strict"] >= row["legs"]:
            continue                      # 只统计「缺了腿」的实例
        pos = {fid[order[k][j]]: j + 1 for j in range(min(800, len(fid)))}
        lines = "".join(blk.get(x["iid"], []))
        for leg in x["legs"]:
            ranks_leg = [pos[f] for f in leg if f in pos]
            if not ranks_leg:
                continue
            r = min(ranks_leg)
            # 这条腿在不在块里（用事实原文的前 12 字做粗判）
            present = any(txt_of[f][:12] in lines for f in leg if f in txt_of)
            for name, lo, hi in buckets:
                if lo <= r <= hi:
                    stat[name]["n"] += 1
                    stat[name]["inBlock"] += 1 if present else 0
                    break
            ranks.append(r)

    print(f"## 缺腿时，这条腿最好那条事实的离线名次（4800/vec，{len(ranks)} 条缺腿）\n")
    print("| 离线名次 | 缺腿数 | 其中恰好在块里 | 说明 |")
    print("|---|---|---|---|")
    note = {"1-5": "排名很好却丢了 → 拼块规则的问题",
            "6-20": "也还好 → 拼块/去重吃掉了",
            "21-50": "在池子边缘 → 预算或截断",
            "51-200": "检索能吃到的极限附近",
            ">200": "检索根本够不到 → 渲染层无解"}
    for name, _lo, _hi in buckets:
        s = stat[name]
        print(f"| {name} | {s['n']} | {s['inBlock']} | {note[name]} |")
    if ranks:
        rs = np.array(ranks)
        print(f"\n中位名次 {int(np.median(rs))}｜p25 {int(np.percentile(rs,25))}｜p75 {int(np.percentile(rs,75))}"
              f"｜前 20 名内 {int((rs<=20).sum())}/{len(rs)}｜前 50 名内 {int((rs<=50).sum())}/{len(rs)}")
        out = {"ranks": ranks}
        json.dump(out, open(os.path.join(HERE, "ab-rank-result.json"), "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
