"""清洗「答案尾部挂着一个孤零零的括号」的脏答案，只重判这些小样本。

引用从句被剥掉后，soft/free 两种收口会留下 `HHKB Pro 2（` 这种尾巴。对判分不公平，所以：
清尾巴 → 只对受影响的 (iid, arm) 重新调一次裁判 → 合并回原来的 rejudge 文件。

用法：python bench/_clean_rejudge.py --answers bench/ab-answer-ids-soft.json --rejudge bench/ab-rejudge-ids-soft.json
"""
import argparse
import json
import os
import re
import time
import urllib.request
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

TAIL = re.compile(r"[\s（(【\[]+$")


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, user, timeout=60):
    body = {"model": model, "messages": [{"role": "system", "content": SYS},
                                         {"role": "user", "content": user}],
            "max_tokens": 60, "temperature": 0, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return (json.loads(r.read().decode("utf-8"))["choices"][0]
                        ["message"].get("content") or "").strip()
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return ""


def parse(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return None
    try:
        v = str(json.loads(m.group(0)).get("v", "")).lower()
        return v if v in ("ok", "partial", "wrong", "abstain") else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--rejudge", required=True)
    ap.add_argument("--conc", type=int, default=12)
    args = ap.parse_args()

    d = json.load(open(os.path.join(HERE, "..", args.answers), encoding="utf-8")) \
        if not os.path.isabs(args.answers) else json.load(open(args.answers, encoding="utf-8"))
    dirty = []
    for r in d["rows"]:
        new = TAIL.sub("", r["answer"]).strip()
        if new != r["answer"]:
            r["answer"] = new
            dirty.append(r)
    print(f"清洗 {len(dirty)} 条尾巴", flush=True)

    rj = json.load(open(args.rejudge, encoding="utf-8"))
    idx = {(x["iid"], x["arm"]): x for x in rj}
    key = load_key()
    base = "https://api.deepseek.com"
    model = "deepseek-flash"

    def job(r):
        user = f"【问题】{r['q']}\n【金标】{str(r['gold'])[:200]}\n【答案】{str(r['answer'])[:120]}"
        return r, call(base, key, model, user)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        res = list(ex.map(job, dirty))
    changed = 0
    for r, txt in res:
        v = parse(txt)
        if v and (r["iid"], r["arm"]) in idx:
            if idx[(r["iid"], r["arm"])]["llm"] != v:
                changed += 1
            idx[(r["iid"], r["arm"])]["llm"] = v
    json.dump(rj, open(args.rejudge, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(d, open(args.answers, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"重判 {len(res)} 条（{time.time()-t0:.0f}s），判定变化 {changed} 条")


if __name__ == "__main__":
    main()
