"""模型当裁判：机械判分对「换词」「只答实体名」「金标是出处」都会误判，这里重判一遍。

输入 ab-answer-result.json（每条已有 q / gold / answer），输出每条的 llm verdict：
  ok      答案与金标等价（允许同义改写、只答实体名、数字单位差异）
  partial 答对一部分（多腿题只答一腿）
  wrong   与金标矛盾或答非所问
  abstain 回答「不知道」

用法：python bench/ab-rejudge-llm.py --conc 12
"""
import argparse
import json
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = r"E:\dshxz\hindsight\.env"

SYS = ("你是评测裁判。给你问题、金标（可能是原句、也可能是出处指针）、模型的答案。"
       "判断模型答案是否算答对。规则："
       "①允许同义改写、允许只答出关键实体或数字；"
       "②金标若是时间地点指针（如 06:11-01 09:06），只要答案与问题相关且不矛盾就算 partial；"
       "③多腿问题只答出一部分算 partial；"
       "④答案说「不知道」「材料里没有」算 abstain；"
       "只输出一行 JSON：{\"v\":\"ok|partial|wrong|abstain\",\"why\":\"≤15字\"}")


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, user, timeout=60):
    body = {"model": model,
            "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
            "max_tokens": 60, "temperature": 0, "thinking": {"type": "disabled"}}
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


def parse(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return "unknown"
    try:
        v = str(json.loads(m.group(0)).get("v", "")).lower()
        return v if v in ("ok", "partial", "wrong", "abstain") else "unknown"
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(HERE, "ab-rejudge.json"))
    ap.add_argument("--in", dest="inp", default=os.path.join(HERE, "ab-answer-result.json"))
    args = ap.parse_args()

    d = json.load(open(args.inp, encoding="utf-8"))
    rows = [r for r in d["rows"] if r["arm"] != "nil"]
    jobs = []
    for r in rows:
        user = f"【问题】{r['q']}\n【金标】{str(r['gold'])[:200]}\n【答案】{str(r['answer'])[:120]}"
        jobs.append((r, user))

    key = load_key()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        out = list(ex.map(lambda j: call(args.base, key, args.model, j[1]), jobs))
    print(f"{len(jobs)} 条重判，{time.time()-t0:.0f}s", flush=True)

    by_arm = defaultdict(Counter)
    res = []
    for (r, _u), txt in zip(jobs, out):
        v = parse(txt)
        by_arm[r["arm"]][v] += 1
        res.append({"iid": r["iid"], "arm": r["arm"], "kind": r["kind"],
                    "mech": r.get("verdict") or ("abstain" if r.get("abstain") else ""),
                    "llm": v, "answer": r["answer"]})
    json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n## 每臂：机械判分 vs 模型判分（ok+partial 都算对）\n")
    print("| 臂 | 机械 ok | 模型 ok | 模型 ok+partial | 模型 wrong | 模型 abstain |")
    print("|---|---|---|---|---|---|")
    order = []
    for r in res:
        if r["arm"] not in order:
            order.append(r["arm"])
    for arm in order:
        c = by_arm[arm]
        n = sum(c.values())
        mech = sum(1 for r in res if r["arm"] == arm and r["mech"] == "ok")
        print(f"| {arm} | {mech}/{n} ({100*mech/n:.1f}%) | {c['ok']}/{n} ({100*c['ok']/n:.1f}%) | "
              f"{c['ok']+c['partial']}/{n} ({100*(c['ok']+c['partial'])/n:.1f}%) | "
              f"{c['wrong']} | {c['abstain']} |")


if __name__ == "__main__":
    main()
