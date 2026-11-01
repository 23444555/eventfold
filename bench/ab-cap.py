"""固定字数上限下的「结构」实验：向量命中的事件，把同一件事的兄弟事实补进来，值不值那点字数。

上一轮 12 臂的结论：并集 `episodePlusVec` 全铺齐 83.9%，但把向量臂填到同样字数（`vecBudget`
80.4%）就统计分不开（8 胜 4 负，p=0.39）——并集的收益可能只是「字数更多」。但那个对照有个漏洞：
`vecBudget` 是不分事件地往里塞事实，而并集换来的是**同一件事的兄弟事实**（多腿题真正要的）。
这一轮把这两件事分开，全部放在同一个字数上限 B 下比：

  臂
    vec          按余弦排序填到 B                                  （纯检索的付费版）
    ep           档案块按自身顺序截断到 B                           （纯结构，没有向量）
    structFirst  先铺「向量命中事件」的结构（含兄弟），再用向量事实填  （结构优先，看会不会饿死检索）
    sib25/sib50  先向量填到 B 的 75%/50%，再把命中事件的兄弟补进来，
                 最后向量填满                                       （给结构留四分之一 / 一半）

跨臂去重：归一化后相等或互相包含的行只留一次，所以「兄弟事实」是净增的，不是同一句话换个说法。

用法：
  python bench/ab-cap.py --caps 1200,2400,4800 --reserves 0.25,0.5 --out bench/ab-cap-result.json
"""
import argparse
import importlib.util
import json
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_arms():
    path = os.path.join(HERE, "ab-arms.py")
    spec = importlib.util.spec_from_file_location("ab_arms", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load_arms()
norm = A.norm
score = A.score
agg = A.agg
binom_two_sided = A.binom_two_sided
DATE_RE = A.DATE_RE
MODEL = A.MODEL


def add_line(line, keep, keys, cap, used):
    """放一行进块：去重（相等或互相包含）→ 字数够就收。返回新的 used。"""
    n = norm(line)
    if not n:
        return used
    for k in keys:
        if n == k or n in k or k in n:
            return used
    if used + len(line) + 1 > cap:
        return used
    keep.append(line)
    keys.append(n)
    return used + len(line) + 1


def fill_facts(ids, line_of, keep, keys, cap, used):
    for fid in ids:
        used = add_line(line_of[fid], keep, keys, cap, used)
    return used


def fill_events(hit_events, event_facts, line_of, keep, keys, cap, used):
    for ev in hit_events:
        for fid in event_facts.get(ev, ()):
            used = add_line(line_of[fid], keep, keys, cap, used)
    return used


def build_arms(vec_ranked, line_of, ep_lines, hit_events, event_facts, cap, reserves):
    order = [fid for fid, _s in vec_ranked]
    out = {}

    # vec：纯向量填到 B
    keep, keys, used = [], [], 0
    fill_facts(order, line_of, keep, keys, cap, used)
    out["vec"] = keep

    # ep：档案块自身顺序截断到 B
    keep, keys, used = [], [], 0
    for line in ep_lines:
        used = add_line(line, keep, keys, cap, used)
    out["ep"] = keep

    # structFirst：结构优先（只铺向量命中的事件，含兄弟事实），剩余给向量
    keep, keys, used = [], [], 0
    used = fill_events(hit_events, event_facts, line_of, keep, keys, cap, used)
    fill_facts(order, line_of, keep, keys, cap, used)
    out["structFirst"] = keep

    # sib<r>：向量先填到 B*(1-r)，再补命中事件的兄弟，最后向量填满
    for r in reserves:
        keep, keys, used = [], [], 0
        first = int(cap * (1 - r))
        used = fill_facts(order, line_of, keep, keys, first, used)
        used = fill_events(hit_events, event_facts, line_of, keep, keys, cap, used)
        fill_facts(order, line_of, keep, keys, cap, used)
        out[f"sib{int(round(r * 100))}"] = keep
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-cap-result.json"))
    ap.add_argument("--archive", default=os.path.join(HERE, "..", "..", "p0-archive", "archive.json"))
    ap.add_argument("--caps", default="1200,2400,4800", help="字数上限，逗号分隔")
    ap.add_argument("--reserves", default="0.25,0.5", help="给结构留的比例，逗号分隔")
    ap.add_argument("--pool", type=int, default=400)
    ap.add_argument("--seed-topk", type=int, default=25, help="用向量 top-N 条事实推命中事件")
    ap.add_argument("--device", default="")
    args = ap.parse_args()
    caps = [int(c) for c in args.caps.split(",") if c.strip()]
    reserves = [float(r) for r in args.reserves.split(",") if r.strip()]
    arms = ["vec", "ep", "structFirst"] + [f"sib{int(round(r * 100))}" for r in reserves]

    d = json.load(open(args.gen, encoding="utf-8"))
    inst, corpus = d["instances"], d["corpus"]
    text_of = {f["i"]: f["t"] for f in corpus}
    # 行里不带日期前缀：实例里的档案行本来就剥了日期，两边同口径才可比（也才去得掉重）。
    line_of = {f["i"]: f["t"] for f in corpus}

    snap = json.load(open(args.archive, encoding="utf-8"))
    event_facts, fact2ev = {}, {}
    for ev in snap.get("events", []):
        ids = [int(f["i"]) for f in ev.get("facts", []) if isinstance(f, dict) and f.get("i") is not None]
        event_facts[ev["id"]] = ids
        for i in ids:
            fact2ev.setdefault(i, ev["id"])
    print(f"实例 {len(inst)}｜事实 {len(corpus)}｜事件 {len(event_facts)}｜上限 {caps}｜留额 {reserves}", flush=True)

    import torch
    from sentence_transformers import SentenceTransformer
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

    rows = {c: {a: [] for a in arms} for c in caps}
    for k, x in enumerate(inst):
        order = np.argsort(-sims[k])[: args.pool]
        vec_ranked = [(corpus[i]["i"], float(sims[k][i])) for i in order]
        hit_events = []
        for i in order[: args.seed_topk]:
            ev = fact2ev.get(int(corpus[i]["i"]))
            if ev and ev not in hit_events:
                hit_events.append(ev)
        ep_lines = [norm(t) for t in x["episode"]]
        for cap in caps:
            picked = build_arms(vec_ranked, line_of, ep_lines, hit_events, event_facts, cap, reserves)
            for name, lines in picked.items():
                s, nr, missing = score(x["legs"], lines, text_of)
                rows[cap][name].append({
                    "iid": x["iid"], "baseId": x["baseId"], "kind": x["kind"],
                    "legs": len(x["legs"]), "strict": s, "near": nr,
                    "chars": sum(len(l) + 1 for l in lines), "facts": len(lines),
                    "missing": missing,
                })
        if (k + 1) % 20 == 0:
            print(f"  {k + 1}/{len(inst)}", flush=True)

    res = {"instances": len(inst), "caps": caps, "arms": arms, "reserves": reserves,
           "summary": {}, "significance": {}}
    for cap in caps:
        summary = {a: {"all": agg(rows[cap][a]),
                       "base": agg([r for r in rows[cap][a] if r["kind"] == "base"]),
                       "variant": agg([r for r in rows[cap][a] if r["kind"] == "variant"])}
                   for a in arms}
        res["summary"][str(cap)] = summary
        sig = {}
        v = {r["iid"]: r for r in rows[cap]["vec"]}
        for name in arms:
            if name == "vec":
                continue
            a = {r["iid"]: r for r in rows[cap][name]}
            win = sum(1 for i in v if a[i]["strict"] == a[i]["legs"] and v[i]["strict"] < v[i]["legs"])
            lose = sum(1 for i in v if a[i]["strict"] < a[i]["legs"] and v[i]["strict"] == v[i]["legs"])
            legs_a = sum(max(0, a[i]["strict"] - v[i]["strict"]) for i in v)
            legs_v = sum(max(0, v[i]["strict"] - a[i]["strict"]) for i in v)
            sig[name] = {"queriesWin": win, "queriesLose": lose,
                         "pQuery": round(binom_two_sided(min(win, lose), win + lose), 5),
                         "legsOnlyArm": legs_a, "legsOnlyVec": legs_v,
                         "pLeg": round(binom_two_sided(min(legs_a, legs_v), legs_a + legs_v), 6)}
        res["significance"][str(cap)] = sig
        res.setdefault("perQuery", {})[str(cap)] = rows[cap]

    json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    n = len(inst)
    total_legs = sum(len(x["legs"]) for x in inst)
    print(f"\n## 固定上限（{n} 实例 / {total_legs} 腿次）\n")
    print("| 上限 | 臂 | 全铺齐 | 腿齐 | 平均字数 | 平均条数 | ≈token | 每千字铺齐 |")
    print("|---|---|---|---|---|---|---|---|")
    for cap in caps:
        for a in arms:
            s = res["summary"][str(cap)][a]["all"]
            per_k = 1000 * s["full"] / max(1, s["chars"] * n / 1000) if s["chars"] else 0
            print(f"| {cap} | {a} | {s['full']}/{n} ({100*s['full']/n:.1f}%) "
                  f"| {s['legs']}/{s['legsTotal']} ({100*s['legs']/s['legsTotal']:.1f}%) "
                  f"| {s['chars']} | {s['facts']} | {int(s['chars']*0.65)} | {per_k:.2f} |")
    print("\n## 原问 vs 改写（严格口径全铺齐）\n")
    print("| 上限 | 臂 | 原问 44 | 改写 68 |")
    print("|---|---|---|---|")
    for cap in caps:
        for a in arms:
            b, ve = res["summary"][str(cap)][a]["base"], res["summary"][str(cap)][a]["variant"]
            print(f"| {cap} | {a} | {b['full']}/{b['n']} ({100*b['full']/b['n']:.1f}%) "
                  f"| {ve['full']}/{ve['n']} ({100*ve['full']/ve['n']:.1f}%) |")
    print("\n## 显著性（同上限下 vs vec）\n")
    print("| 上限 | 对比 | 题上赢 | 题上输 | p(题) | 腿只这臂有 | 腿只 vec 有 | p(腿) |")
    print("|---|---|---|---|---|---|---|---|")
    for cap in caps:
        for a in arms:
            if a == "vec":
                continue
            s = res["significance"][str(cap)][a]
            print(f"| {cap} | vec vs {a} | {s['queriesWin']} | {s['queriesLose']} | {s['pQuery']} "
                  f"| {s['legsOnlyArm']} | {s['legsOnlyVec']} | {s['pLeg']} |")
    print(f"\n写出 {args.out}")


if __name__ == "__main__":
    main()
