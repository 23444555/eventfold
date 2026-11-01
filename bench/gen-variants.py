"""重造改写题库：只保留「答案不变」的改写（v4）。

旧数据集 `novels/variants-3-src.json` 里 25% 的改写换了问的东西、24% 只对一半，却仍用原题
金标判分 —— 这是「改写掉 57 点」里最大的一块假象。

这版流程：每题生成 3 个候选 → 模型逐条校验「原答能否直接回答改写问」→ **只留 same**，
每题最多 2 条。校验用的判断比旧版更严：除了「问同一件事」，还要求改写问没有新增限定
（新的时间/数量/第三人），否则原答不够答。

用法：
  python bench/gen-variants.py --per 3 --keep 2 --conc 12
产出：
  bench/variants-v4.json      ab-gen.mjs 认识的形态 [{id, variants:[...]}]
  bench/variants-v4-log.json  每条候选的判定与理由（含被丢弃的）
"""
import argparse
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(HERE))
ENV = r"E:\dshxz\hindsight\.env"

GEN_SYS = (
    "你在造检索评测用的改写题。给一句原问，写一个「问的东西一模一样、但说法完全不同」的问法。"
    "硬规则："
    "①答案必须与原问的答案一字不差地相同，不许换角度、不许追问新细节；"
    "②人名换成角色或指代（「周工」→「管这摊的那位」、「李四」→「跑脚本那位」），不许直接点名；"
    "③术语换成日常说法（「相似度阈值」→「那个卡得太死的数」），但不许改成另一个概念；"
    "④不许新增时间、数量、人名等任何限定；不许把答案写进问题里；"
    "⑤15~30 字，只输出这一句问句，不要解释、不要引号。"
)

JUDGE_SYS = (
    "你在校验改写题是否等价。给你原问、原答、改写问。只有当「原答可以直接回答改写问、"
    "不需要任何额外信息」时才判 same；如果改写问要的东西变了、多了限定、或者原答只是部分相关，"
    "分别判 different / different / partial。只输出一行 JSON："
    '{"v":"same|partial|different","why":"≤15字"}'
)


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, sys_prompt, user, temp, max_tokens=120, timeout=60):
    body = {"model": model,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temp, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            return (d["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return ""


def clean_q(s):
    s = (s or "").strip().split("\n")[0]
    s = re.sub(r"^[\s\"'「『]|[\s\"'」』]$", "", s)
    s = re.sub(r"^(改写|问句|答案)[:：]\s*", "", s)
    return s.strip()


def parse_verdict(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return "unknown", ""
    try:
        d = json.loads(m.group(0))
        v = str(d.get("v", "")).lower()
        return (v if v in ("same", "partial", "different") else "unknown"), str(d.get("why", ""))[:20]
    except Exception:
        return "unknown", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa", default=os.path.join(WORKSPACE, "novels", "qa-v2.json"))
    ap.add_argument("--req", default=os.path.join(WORKSPACE, "novels", "required-sets.json"))
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen.json"))
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--per", type=int, default=3)
    ap.add_argument("--keep", type=int, default=2)
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--temp", type=float, default=0.9)
    ap.add_argument("--out", default=os.path.join(HERE, "variants-v4.json"))
    args = ap.parse_args()

    key = load_key()
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    gen = json.load(open(args.gen, encoding="utf-8"))
    facts = {c["i"]: c["t"] for c in gen["corpus"]}
    bases = [x for x in gen["instances"] if x["kind"] == "base"]
    print(f"原题 {len(bases)}｜每题 {args.per} 候选｜每题最多留 {args.keep}", flush=True)

    # 阶段一：生成
    jobs = [(x["iid"], args.per) for x in bases for _ in range(args.per)]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        raw = list(ex.map(lambda j: call(args.base, key, args.model, GEN_SYS, qa[j[0]]["q"],
                                         args.temp, 80), jobs))
    print(f"生成 {len(raw)} 条（{time.time()-t0:.0f}s）", flush=True)

    # 阶段二：逐条校验（原问 / 原答 / 改写）
    pairs = []
    for (qid, _n), txt in zip(jobs, raw):
        v = clean_q(txt)
        if not v or len(v) < 6:
            continue
        q = qa[qid]
        gold = (q.get("answer") or "").strip() or " / ".join(
            facts[f] for leg in (json.load(open(args.req, encoding="utf-8")).get(qid, {}).get("legs") or [[q["anchor_unit"]]])
            for f in leg[:1] if f in facts)[:160]
        user = f"【原问】{q['q']}\n【原答】{gold}\n【改写问】{v}"
        pairs.append((qid, v, gold, user))
    print(f"有效候选 {len(pairs)}", flush=True)

    t1 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        judged = list(ex.map(lambda p: call(args.base, key, args.model, JUDGE_SYS, p[3], 0.0, 60), pairs))
    print(f"校验 {len(judged)} 条（{time.time()-t1:.0f}s）", flush=True)

    keep = {}
    log = []
    for (qid, v, _gold, _u), txt in zip(pairs, judged):
        verdict, why = parse_verdict(txt)
        ok = verdict == "same" and v != qa[qid]["q"]
        log.append({"id": qid, "variant": v, "verdict": verdict, "why": why, "kept": False})
        if ok and len(keep.get(qid, [])) < args.keep:
            keep.setdefault(qid, []).append(v)
            log[-1]["kept"] = True

    out = [{"id": qid, "variants": vs} for qid, vs in keep.items()]
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(log, open(args.out.replace(".json", "-log.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    from collections import Counter
    c = Counter(r["verdict"] for r in log)
    nv = sum(len(v) for v in keep.values())
    print(f"\n判定：same {c['same']} / partial {c['partial']} / different {c['different']} / "
          f"unknown {c['unknown']}｜通过率 {100*c['same']/max(len(log),1):.0f}%")
    print(f"保留：{nv} 条改写，覆盖 {len(keep)}/{len(bases)} 道原题（每题最多 {args.keep}）")
    print(f"写出 {args.out}")


if __name__ == "__main__":
    main()
