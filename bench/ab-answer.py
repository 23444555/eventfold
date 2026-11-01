"""让模型真答一遍：同样的问题、同样（或不同）的材料，答案对不对。

前面所有实验量的是「块里有没有那条事实」（证据召回）。这一轮换成端到端：
把块塞进 prompt，让模型只根据材料回答，再机械判对错。想回答三件事——

  1. 证据齐不齐，和答对没答对，到底差多少？（如果差距很大，前面的 A/B 结论要打折）
  2. 材料给多给少，答对率怎么变？（4.8k 字里塞 120 条事实，可能反而更糟）
  3. 结构（兄弟事实）在答题层面有没有用？

臂（都取自 ab-cap.py 已经算好的块）：
  nil        不给材料（测瞎猜/沉默率）
  1200/vec   小预算纯向量
  2400/vec   中预算纯向量
  4800/vec   大预算纯向量
  4800/sib25 大预算、结构做补充（留 25%）
  4800/ep    只给档案块（约 2k 字，天然短）

用法：
  python bench/ab-answer.py --limit 4 --conc 4          # 冒烟：每臂 4 个实例
  python bench/ab-answer.py --conc 10 --out bench/ab-answer-result.json
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))
ENV = r"E:\dshxz\hindsight\.env"

SYSTEM = (
    "你是记忆系统的阅读器。只根据【材料】回答，材料里没有这条信息就只回「不知道」。"
    "只输出答案本身，不超过 20 个字，不要解释，不要复述材料。"
)


def load_cfg():
    cfg = {}
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k] = v.strip().strip('"').strip("'")
    return cfg


PUNCT = "，。、；：！？（）【】「」《》·…—～\"'`*#-"


def norm_text(s):
    s = str(s or "")
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:          # 全角 → 半角
            o -= 0xFEE0
            ch = chr(o)
        elif o == 0x3000:
            ch = " "
        out.append(ch)
    s = "".join(out)
    s = re.sub(r"\s+", "", s)
    return "".join(ch for ch in s if ch not in PUNCT)


def gold_core(g):
    """金标常写成「185 分钟（15+40+130）」，括号前那段才是要答的东西。"""
    return re.split(r"[（(]", str(g or ""))[0]


FILLER = re.compile(r"[的了那个就是在这只把和与]")


def _bg(s):
    z = FILLER.sub("", s)
    return set(z[i:i + 2] for i in range(len(z) - 1))


def _jac(a, b):
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter or 1)


PTR = re.compile(r"^\d{1,2}:\d{2}-\d{2}[^ ]*\s+\d{2}:\d{2}")


def is_pointer(ev):
    """`07:11-30 18:01` 这种是出处（文件:日期 时间），不是答案本身。"""
    return bool(PTR.match(str(ev or "").strip()))


def judge(q, answer):
    """返回 'ok' / 'abstain' / 'wrong'。机械判，不调第二个模型。

    金标有三类：44 道原题里 15 道有显式 answer；29 道只有 evidence（原文那一句）；
    13 道的 evidence 是「07:11-30 18:01」这种出处指针，真答案在 anchors 里。
    """
    a = norm_text(answer)
    if not a or re.search(r"不知道|无法|没提到|材料中没有|没有相关|信息不足", a):
        return "abstain"
    if str(q.get("type")) == "unanswerable":
        return "wrong"          # 这题本来就答不了，模型说了别的 = 编

    gold = str(q.get("answer") or "").strip()
    if gold:
        if norm_text(gold_core(gold)) in a:
            return "ok"
        digits = re.findall(r"\d+", gold)
        if digits and all(d in a for d in digits):
            return "ok"

    ev = norm_text(q.get("evidence") or "")
    if ev and not is_pointer(q.get("evidence")):
        if a in ev or ev in a:
            return "ok"
        # 同一句话的轻微改写（「那个附件里」vs「附件里」）也算对
        bg_a, bg_e = _bg(a), _bg(ev)
        if bg_a and bg_e and _jac(bg_a, bg_e) >= 0.6:
            return "ok"
        for run in re.findall(r"[a-z0-9]{2,}", ev):
            if any(ch.isdigit() for ch in run) and run in a:
                return "ok"
    for an in q.get("anchors") or []:
        na = norm_text(an)
        if len(na) < 2:
            continue
        if a in na or na in a:
            return "ok"
        bg_a, bg_n = _bg(a), _bg(na)
        if bg_a and bg_n and _jac(bg_a, bg_n) >= 0.55:
            return "ok"
    return "wrong"


def call_llm(base, key, model, prompt, timeout=90):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "max_tokens": 256,
        "temperature": 0,
        "thinking": {"type": "disabled"},
    }
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(3):
        req = urllib.request.Request(
            base + "/chat/completions", data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            msg = d["choices"][0]["message"]
            return {"answer": (msg.get("content") or "").strip(),
                    "reasoning": (msg.get("reasoning_content") or "")[:200],
                    "usage": d.get("usage") or {}, "lat": round(time.time() - t0, 2)}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:160]}"
            time.sleep(1.5 * (attempt + 1))
    return {"answer": "", "error": last, "usage": {}, "lat": None}


def rejudge(path, qa):
    """拿已有答案重新判一遍（改判分规则时用，不重调模型、不花 token）。"""
    d = json.load(open(path, encoding="utf-8"))
    changed = 0
    for r in d["rows"]:
        q = qa.get(r["baseId"], {})
        v = judge(q, r.get("answer", ""))
        if v != r["verdict"]:
            changed += 1
        r["verdict"] = v
    summary = {}
    for a in d["arms"]:
        rs = [r for r in d["rows"] if r["arm"] == a]
        n = len(rs)
        ok = sum(1 for r in rs if r["verdict"] == "ok")
        ab = sum(1 for r in rs if r["verdict"] == "abstain")
        base = [r for r in rs if r["kind"] == "base"]
        var = [r for r in rs if r["kind"] == "variant"]
        summary[a] = {
            "n": n, "ok": ok, "abstain": ab, "wrong": n - ok - ab,
            "acc": round(ok / n, 4) if n else 0,
            "baseAcc": round(sum(1 for r in base if r["verdict"] == "ok") / len(base), 4) if base else 0,
            "variantAcc": round(sum(1 for r in var if r["verdict"] == "ok") / len(var), 4) if var else 0,
            "chars": round(sum(r["chars"] for r in rs) / n) if n else 0,
            "lat": round(sum(r["lat"] or 0 for r in rs) / n, 2) if n else 0,
        }
    d["summary"] = summary
    d["rejudged"] = changed
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"## 重新判分（改判 {changed} 条）\n")
    print("| 臂 | 答对 | 沉默 | 答错 | 准确率 | 原问 | 改写 |")
    print("|---|---|---|---|---|---|---|")
    for a in d["arms"]:
        s = summary[a]
        print(f"| {a} | {s['ok']}/{s['n']} | {s['abstain']} | {s['wrong']} "
              f"| {100*s['acc']:.1f}% | {100*s['baseAcc']:.1f}% | {100*s['variantAcc']:.1f}% |")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-answer-result.json"))
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="")
    ap.add_argument("--conc", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help="每个臂只跑前 N 个实例（冒烟用）")
    ap.add_argument("--arms", default="nil,1200/vec,2400/vec,4800/vec,4800/sib25,4800/ep")
    ap.add_argument("--rejudge", action="store_true", help="重判 --out 里已有的答案，不调模型")
    args = ap.parse_args()

    qa_all = {}
    for x in json.load(open(args.qa, encoding="utf-8"))["questions"]:
        qa_all[x["id"]] = x
    if args.rejudge:
        rejudge(args.out, qa_all)
        return

    cfg = load_cfg()
    key = cfg["HINDSIGHT_API_LLM_API_KEY"]
    model = args.model or cfg.get("HINDSIGHT_API_LLM_MODEL", "deepseek-flash")

    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = gen["instances"]
    if args.limit:
        inst = inst[: args.limit]

    arms = []
    for a in args.arms.split(","):
        a = a.strip()
        if not a:
            continue
        if a == "nil":
            arms.append(("nil", None, None))
        else:
            cap, name = a.split("/")
            arms.append((a, cap, name))

    jobs = []
    for a, cap, name in arms:
        for x in inst:
            q = qa.get(x["baseId"], {})
            lines = [] if cap is None else blocks[cap][name].get(x["iid"], [])
            material = "\n".join(f"- {l}" for l in lines) if lines else "（无材料）"
            prompt = f"【材料】\n{material}\n\n【问题】{x['q']}\n\n【答案】"
            jobs.append({"arm": a, "iid": x["iid"], "baseId": x["baseId"], "kind": x["kind"],
                         "q": x["q"], "gold": q.get("answer", "") or q.get("evidence", ""),
                         "qtype": q.get("type", ""), "chars": sum(len(l) + 1 for l in lines),
                         "facts": len(lines), "prompt": prompt, "_qa": q})

    print(f"模型 {model}｜臂 {[a for a, _, _ in arms]}｜实例 {len(inst)}｜请求 {len(jobs)}｜并发 {args.conc}", flush=True)
    t0 = time.time()

    def work(j):
        r = call_llm(args.base, key, model, j["prompt"])
        j.update(r)
        j["verdict"] = judge(j["_qa"], r.get("answer", ""))
        j.pop("_qa", None)
        j.pop("prompt", None)
        return j

    done = 0
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        results = []
        for j in ex.map(work, jobs):
            results.append(j)
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(jobs)}  {time.time()-t0:.0f}s", flush=True)

    elapsed = time.time() - t0
    sum_in = sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in results)
    sum_out = sum((r.get("usage") or {}).get("completion_tokens", 0) for r in results)
    errs = [r for r in results if r.get("error")]

    summary = {}
    for a, _c, _n in arms:
        rs = [r for r in results if r["arm"] == a]
        n = len(rs)
        ok = sum(1 for r in rs if r["verdict"] == "ok")
        ab = sum(1 for r in rs if r["verdict"] == "abstain")
        base = [r for r in rs if r["kind"] == "base"]
        var = [r for r in rs if r["kind"] == "variant"]
        summary[a] = {
            "n": n, "ok": ok, "abstain": ab, "wrong": n - ok - ab,
            "acc": round(ok / n, 4) if n else 0,
            "baseAcc": round(sum(1 for r in base if r["verdict"] == "ok") / len(base), 4) if base else 0,
            "variantAcc": round(sum(1 for r in var if r["verdict"] == "ok") / len(var), 4) if var else 0,
            "chars": round(sum(r["chars"] for r in rs) / n) if n else 0,
            "lat": round(sum(r["lat"] or 0 for r in rs) / n, 2) if n else 0,
        }

    out = {"model": model, "elapsed": round(elapsed, 1), "requests": len(jobs),
           "errors": len(errs), "tokensIn": sum_in, "tokensOut": sum_out,
           "arms": [a for a, _, _ in arms], "summary": summary, "rows": results}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n## 端到端答题（{len(inst)} 实例／臂，{elapsed:.0f}s，错误 {len(errs)}）\n")
    print("| 臂 | 平均材料字数 | 答对 | 沉默 | 答错 | 准确率 | 原问 | 改写 | 平均延迟 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for a, _c, _n in arms:
        s = summary[a]
        print(f"| {a} | {s['chars']} | {s['ok']}/{s['n']} | {s['abstain']} | {s['wrong']} "
              f"| {100*s['acc']:.1f}% | {100*s['baseAcc']:.1f}% | {100*s['variantAcc']:.1f}% | {s['lat']}s |")
    print(f"\ntoken：入 {sum_in} / 出 {sum_out}｜写出 {args.out}")


if __name__ == "__main__":
    main()
