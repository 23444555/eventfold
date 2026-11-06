"""提示线索检索（cue）：先让模型说出「想被提醒什么」，再用这些线索分别检索、取并集。

动机：人是靠线索回忆的（那天晚上 / 群里 / 回滚 / 谁提的），不是拿今天的问题原句去比相似度。
HyDE 是在猜答案、两段式是在候选里二次筛选，两者都没动「检索入口」；这一版直接换检索键。

四个臂（同语料、同字数上限、同判分）：
  vec      问题原句检索（基线）
  cue      模型写的 1~3 条提醒线索，各自检索，按每句的最大相似度排序
  cuevec   cue 与 vec 的名次融合（RRF，k=60）
  cueRule  不调模型：把问句切成片段当线索（对照，防止「收益全来自多查几次」）

用法：
  python bench/ab-cue.py --caps 1200,4800 --out bench/ab-cue-result.json
"""
import argparse
import importlib.util
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load("ab_arms", os.path.join(HERE, "ab-arms.py"))
norm, score, agg, binom_two_sided = A.norm, A.score, A.agg, A.binom_two_sided
MODEL = A.MODEL
DATE_RE = A.DATE_RE

ENV = r"E:\dshxz\hindsight\.env"
CUE_SYS = (
    "你只做一件事：为下面的问题写出 1~3 条「提醒线索」——如果一个人想回忆起这件事，"
    "别人提哪些词最容易让他想起来（人名、时间、动作、物件、场景）。"
    "每条不超过 12 字，用「、」分隔，只输出线索本身，不要回答问题，不要解释。"
)
SPLIT = re.compile(r"[、，,;；/\n]+")
RULE_SPLIT = re.compile(r"[的了吗呢吧啊是在里上把那这哪个多少怎为什么？?，,。.、]+")


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key in .env")


def call_llm(base, key, model, system, user, max_tokens=80, timeout=60):
    import urllib.request

    body = json.dumps({
        "model": model, "temperature": 0.2, "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            return (d["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return ""


def parse_cues(txt, limit=3, max_len=16):
    parts = [p.strip(" -·。.") for p in SPLIT.split(txt or "")]
    out = []
    for p in parts:
        if not p or len(p) > max_len:
            continue
        if p not in out:
            out.append(p)
    return out[:limit]


def rule_cues(q, limit=5):
    frags = [f for f in RULE_SPLIT.split(q) if 2 <= len(f) <= 8]
    out = []
    for f in frags:
        if f not in out:
            out.append(f)
    return out[:limit]


def rrf_merge(rank_lists, k=60):
    """rank_lists: list of dicts fid -> rank (0-based). Returns fid -> fused score."""
    fused = {}
    for ranks in rank_lists:
        for fid, r in ranks.items():
            fused[fid] = fused.get(fid, 0.0) + 1.0 / (k + r + 1)
    return fused


def fill(fids, line_of, cap, keys=None):
    """按给定顺序填到字数上限，去重（相等或互相包含）。"""
    keys = list(keys or [])
    keep, used = [], 0
    for fid in fids:
        line = line_of.get(fid)
        if not line:
            continue
        n = norm(line)
        if not n:
            continue
        if any(n == k or n in k or k in n for k in keys):
            continue
        if used + len(line) + 1 > cap:
            continue
        keep.append(line)
        keys.append(n)
        used += len(line) + 1
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--caps", default="1200,4800")
    ap.add_argument("--out", default=os.path.join(HERE, "ab-cue-result.json"))
    ap.add_argument("--cues-out", default=os.path.join(HERE, "ab-cue-cues.json"))
    ap.add_argument("--llm-base", default="https://api.deepseek.com")
    ap.add_argument("--llm-model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--regen", action="store_true", help="重新生成线索（默认复用缓存）")
    ap.add_argument("--device", default="")
    args = ap.parse_args()

    caps = [int(c) for c in args.caps.split(",") if c.strip()]
    gen = json.load(open(args.gen, encoding="utf-8"))
    inst, corpus = gen["instances"], gen["corpus"]
    line_of = {f["i"]: f["t"] for f in corpus}
    ids = [f["i"] for f in corpus]

    # ---- 线索：模型生成（带缓存） ----
    cues_path = args.cues_out
    cues = {}
    if os.path.exists(cues_path) and not args.regen:
        cues = json.load(open(cues_path, encoding="utf-8"))
        print(f"线索缓存 {len(cues)} 条 <- {cues_path}", flush=True)
    todo = [x for x in inst if x["iid"] not in cues]
    if todo:
        key = load_key()
        t0 = time.time()

        def gen_one(x):
            txt = call_llm(args.llm_base, key, args.llm_model, CUE_SYS, "【问题】" + x["q"])
            return x["iid"], parse_cues(txt)

        with ThreadPoolExecutor(max_workers=args.conc) as ex:
            for iid, cs in ex.map(gen_one, todo):
                cues[iid] = cs
        print(f"生成线索 {len(todo)} 条 / {time.time()-t0:.0f}s", flush=True)
        json.dump(cues, open(cues_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    empty = [k for k, v in cues.items() if not v]
    if empty:
        print(f"注意：{len(empty)} 个实例没抽到线索，用问句兜底", flush=True)

    # ---- 向量 ----
    import torch
    from sentence_transformers import SentenceTransformer

    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device {dev} | embed {MODEL}", flush=True)
    st = SentenceTransformer(MODEL, device=dev)
    st.max_seq_length = 512
    if dev == "cuda":
        st.half()
    C = st.encode([f["t"] for f in corpus], batch_size=128, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False)
    Q = st.encode([x["q"] for x in inst], batch_size=32, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False)

    cue_texts, cue_owner = [], []
    for k, x in enumerate(inst):
        cs = cues.get(x["iid"]) or [x["q"]]
        for c in cs:
            cue_texts.append(c)
            cue_owner.append(k)
    U = st.encode(cue_texts, batch_size=64, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False)
    print(f"线索 {len(cue_texts)} 条（平均 {len(cue_texts)/len(inst):.1f} 条/题）", flush=True)

    sims_q = (Q @ C.T).astype(np.float32)
    sims_c = (U @ C.T).astype(np.float32)
    cue_rows = [[] for _ in inst]
    for j, owner in enumerate(cue_owner):
        cue_rows[owner].append(sims_c[j])

    rows = {c: {a: [] for a in ("vec", "cue", "cuevec", "cueRule")} for c in caps}
    ranks_for_gold = []   # (iid, vec_rank_of_gold, cue_rank_of_gold)

    for k, x in enumerate(inst):
        legs = x["legs"]
        vec_sim = sims_q[k]
        order_vec = list(np.argsort(-vec_sim))
        rank_vec = {ids[i]: r for r, i in enumerate(order_vec)}

        # cue: 每句取所有线索里的最大相似度
        if cue_rows[k]:
            cue_sim = np.max(np.stack(cue_rows[k], axis=0), axis=0)
        else:
            cue_sim = vec_sim
        order_cue = list(np.argsort(-cue_sim))
        rank_cue = {ids[i]: r for r, i in enumerate(order_cue)}

        # cueRule: 问句切片段，各自检索
        rf = rule_cues(x["q"])
        if rf:
            R = st.encode(rf, batch_size=8, normalize_embeddings=True, convert_to_numpy=True,
                          show_progress_bar=False)
            sims_r = (R @ C.T).astype(np.float32)
            rule_ranks = []
            for row in sims_r:
                oc = list(np.argsort(-row))
                rule_ranks.append({ids[i]: r for r, i in enumerate(oc)})
            fused_rule = rrf_merge(rule_ranks)
            order_rule = sorted(fused_rule, key=lambda f: -fused_rule[f])
        else:
            order_rule = [ids[i] for i in order_vec]

        # cuevec: cue 与 vec 名次融合
        cue_rank_lists = []
        for row in (cue_rows[k] or [vec_sim]):
            oc = list(np.argsort(-row))
            cue_rank_lists.append({ids[i]: r for r, i in enumerate(oc)})
        fused = rrf_merge(cue_rank_lists + [rank_vec])
        order_cuevec = sorted(fused, key=lambda f: -fused[f])

        for cap in caps:
            for name, order in (("vec", [ids[i] for i in order_vec]),
                                ("cue", [ids[i] for i in order_cue]),
                                ("cuevec", order_cuevec),
                                ("cueRule", order_rule)):
                picked = fill(order, line_of, cap)
                s, nr, missing = score(legs, picked, line_of)
                rows[cap][name].append({
                    "iid": x["iid"], "baseId": x["baseId"], "kind": x["kind"],
                    "legs": len(legs), "strict": s, "near": nr,
                    "chars": sum(len(l) + 1 for l in picked), "facts": len(picked),
                })

        # 金标名次（全库，供深尾分析）
        gold = [f for leg in legs for f in leg]
        gr_vec = min((rank_vec.get(f, 10 ** 6) for f in gold), default=10 ** 6)
        gr_cue = min((rank_cue.get(f, 10 ** 6) for f in gold), default=10 ** 6)
        ranks_for_gold.append({"iid": x["iid"], "kind": x["kind"],
                               "vecRank": gr_vec, "cueRank": gr_cue})

    res = {"caps": caps, "arms": ["vec", "cue", "cuevec", "cueRule"], "summary": {}, "sig": {}}
    for cap in caps:
        res["summary"][str(cap)] = {a: agg(rows[cap][a]) for a in rows[cap]}
        v = {r["iid"]: r for r in rows[cap]["vec"]}
        sig = {}
        for a in rows[cap]:
            if a == "vec":
                continue
            ar = {r["iid"]: r for r in rows[cap][a]}
            win = sum(1 for i in v if ar[i]["strict"] == ar[i]["legs"] and v[i]["strict"] < v[i]["legs"])
            lose = sum(1 for i in v if ar[i]["strict"] < ar[i]["legs"] and v[i]["strict"] == v[i]["legs"])
            legs_a = sum(max(0, ar[i]["strict"] - v[i]["strict"]) for i in v)
            legs_v = sum(max(0, v[i]["strict"] - ar[i]["strict"]) for i in v)
            sig[a] = {"win": win, "lose": lose, "legsGain": legs_a, "legsLoss": legs_v,
                      "p": round(binom_two_sided(win, win + lose), 4) if win + lose else None}
        res["sig"][str(cap)] = sig

    cues_lens = [len(cues.get(x["iid"]) or []) for x in inst]
    res["cues"] = {"n": len(cues_lens), "avg": round(sum(cues_lens) / len(cues_lens), 2)}

    # 深尾：全库金标名次的中位数
    import statistics
    med = {}
    for key in ("vecRank", "cueRank"):
        vals = [r[key] for r in ranks_for_gold]
        med[key] = {"median": statistics.median(vals),
                    "p25": statistics.quantiles(vals, n=4)[0],
                    "p75": statistics.quantiles(vals, n=4)[2],
                    "top20": sum(1 for v in vals if v <= 20),
                    "top50": sum(1 for v in vals if v <= 50),
                    "n": len(vals)}
    res["goldRank"] = med
    json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n## 证据铺齐率（严格，同字数上限）\n")
    print("| 上限 | 臂 | 全铺齐 | 腿齐 | 字数 | 对 vec |")
    print("|---|---|---|---|---|---|")
    for cap in caps:
        for a in ("vec", "cue", "cuevec", "cueRule"):
            s = res["summary"][str(cap)][a]
            sig = res["sig"][str(cap)].get(a)
            tag = "-" if not sig else f"{sig['win']}胜{sig['lose']}负 p={sig['p']}"
            print(f"| {cap} | {a} | {s['full']}/{s['n']} ({100*s['full']/s['n']:.1f}%) | "
                  f"{100*s['nearFull']/s['n']:.1f}% | {s['chars']} | {tag} |")
    print("\n## 金标在全库的名次（越小越好）\n")
    for key, v in med.items():
        print(f"{key}: 中位 {v['median']:.0f}  p25 {v['p25']:.0f}  p75 {v['p75']:.0f}  "
              f"top20 {v['top20']}/{v['n']}  top50 {v['top50']}/{v['n']}")
    print(f"\n写出 {args.out}")


if __name__ == "__main__":
    main()
