"""不确定时「问人」：把信息缺失变成一条可执行的约束（日期/人/哪件事），而不是硬猜。

臂：
  single   直接答（bestguess 口径），不许问
  ask      可以问；问了就由「会回答的用户」按金标事实答（模拟真人有这份记忆）
  askVague 可以问；但用户有一半概率答得很模糊（模拟真人记不清），用来算最坏情况

指标：
  - 终答 ok+partial（另用 ab-rejudge-llm.py 复判，与基线同口径）
  - 提问率、无效提问率（问了但金标不全在材料里）、泄露率（用户答里出现了金标答案原文）
  - 多花的 token / 调用数

用法：
  python bench/ab-ask.py --arms single,ask,askVague --conc 12
"""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))
ENV = r"E:\dshxz\hindsight\.env"

SYS_SINGLE = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
              "只要材料里有和问题相关的句子，就给出最可能的答案——措辞不同、需要换算、需要取某个数字，"
              "都照答；只有当材料里完全没有相关句子时才回「不知道」。")

SYS_ASK = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
           "只要材料里有和问题相关的句子，就给出最可能的答案——措辞不同、需要换算、需要取某个数字，都照答。"
           "但如果你发现材料里有多个互相冲突的候选（不同日期、不同人、不同事件），"
           "凭这些分不清，那就先别猜，改成问用户一个能分辨的问题。"
           "需要问就只输出一行：ASK: 你的问题（问一个具体维度，例如哪天、哪个人、哪件事）"
           "不需要问就只输出一行：ANSWER: 你的答案")

SYS_USER = ("你是这个助手的对话对象，你记得下面这些真实发生过的事：\n{gold}\n\n"
            "助手问你一个问题，你用自己的口吻简短回答，不超过 15 字。"
            "规则：只回答他被问到的那一个维度（哪天/哪段时间、哪个人、哪件事、多少个），"
            "不要主动把完整答案或完整句子念出来，不要替他推理，不要给材料里没有的信息。")

SYS_USER_VAGUE = ("你是这个助手的对话对象，下面这些事你隐约有印象：\n{gold}\n\n"
                  "助手问你一个问题，你用自己的口吻简短回答，不超过 15 字。"
                  "你确实记不太清：只给一个模糊的说法（比如「好像是月底那次」「具体日子想不起来了」"
                  "「就是他啊」「我不太确定是哪个」，不要编造确切的日期、数字、人名。")

SYS_FINAL = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
             "下面还有你和用户刚才的对话，用户补充的信息可以当作找材料的线索，但答案仍必须来自材料；"
             "材料里确实没有就回「不知道」。")

# 只在「本来要弃答」时才问：省掉那 28/47 次不必要的客气
SYS_GATED = ("你在读一份带编号的记忆材料，每行形如 [F1234] 内容。只根据材料回答，不超过 20 字。"
             "只要材料里有和问题相关的句子就照答——措辞不同、需要换算、需要取某个数字，都照答。"
             "只有当你本来要回「不知道」（材料里的候选互相冲突、或关键维度缺失、或材料里没有相关句子）时，"
             "才改成问用户一个能分辨的问题，只输出一行：ASK: 你的问题（问一个具体维度，例如哪天、哪个人、哪件事）"
             "否则只输出一行：ANSWER: 你的答案")

SYS_BY_ARM = {"single": SYS_SINGLE, "ask": SYS_ASK, "askVague": SYS_ASK, "askGated": SYS_GATED}

ABSTAIN = re.compile(r"不知道|无法确定|没有相关|材料中没有|没提到|信息不足")
ASK_RE = re.compile(r"^\s*(?:ASK|问)\s*[:：]\s*(.+)$", re.I | re.M)
ANS_RE = re.compile(r"^\s*(?:ANSWER|答)\s*[:：]\s*(.+)$", re.I | re.M)


def load_key():
    for line in open(ENV, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


def call(base, key, model, sysmsg, user, timeout=90, max_tokens=120):
    body = {"model": model,
            "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0, "thinking": {"type": "disabled"}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(base + "/chat/completions", data=data,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            return (d["choices"][0]["message"].get("content") or "").strip(), (d.get("usage") or {})
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return "", {}


def norm(s):
    return re.sub(r"\s+", "", str(s or "").replace("| When:", "").strip())


def clean_ans(txt):
    t = (txt or "").strip()
    m = ANS_RE.search(t)
    if m:
        t = m.group(1)
    return re.sub(r"\s+", " ", t.split("\n")[0]).strip(" -—:：,，\"'「」")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default=os.path.join(HERE, "ab-gen-v4.json"))
    ap.add_argument("--blocks", default=os.path.join(HERE, "ab-cap-blocks-v4.json"))
    ap.add_argument("--qa", default=os.path.join(WS, "novels", "qa-v2.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "ab-ask.json"))
    ap.add_argument("--arm", default="4800/vec", help="上下文臂（默认与基线 bestguess 同源）")
    ap.add_argument("--arms", default="single,ask,askVague")
    ap.add_argument("--base", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--conc", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    key = load_key()
    cap, name = args.arm.split("/")
    gen = json.load(open(args.gen, encoding="utf-8"))
    blocks = json.load(open(args.blocks, encoding="utf-8"))[cap][name]
    qa = {q["id"]: q for q in json.load(open(args.qa, encoding="utf-8"))["questions"]}
    inst = {x["iid"]: x for x in gen["instances"]}
    facts = {f["i"]: f["t"] for f in gen["corpus"]}

    by_text = defaultdict(list)
    for f in gen["corpus"]:
        by_text[norm(f["t"])].append(f["i"])

    def fid_of(line, legs_flat):
        cand = by_text.get(norm(line), [])
        if not cand:
            return None
        for c in cand:
            if c in legs_flat:
                return c
        return min(cand)

    # ---- 准备每个实例的输入 ----
    items = []
    for iid, lines in blocks.items():
        x = inst[iid]
        legs_flat = {f for leg in x["legs"] for f in leg}
        labelled, ids_in = [], set()
        for line in lines:
            fid = fid_of(line, legs_flat)
            if fid is not None:
                ids_in.add(fid)
            labelled.append(f"[F{fid}] {line}" if fid else line)
        gold_facts = "\n".join("- " + facts[f] for f in sorted(legs_flat) if f in facts)
        gold = (qa.get(x["baseId"], {}).get("answer") or "").strip()
        if not gold:
            gold = " / ".join(facts[f] for f in sorted(legs_flat) if f in facts)[:200]
        covered = len(legs_flat & ids_in) / max(1, len(legs_flat))
        items.append({"iid": iid, "q": x["q"], "kind": x["kind"], "gold": gold,
                      "gold_facts": gold_facts, "material": "\n".join(f"- {l}" for l in labelled),
                      "covered": covered, "legs": sorted(legs_flat)})
    if args.limit:
        items = items[: args.limit]
    arms = [a.strip() for a in args.arms.split(",")]
    print(f"{len(items)} 题 × {len(arms)} 臂 = {len(items)*len(arms)} 次首轮｜上下文 {args.arm}｜{args.model}", flush=True)

    def first(m):
        arm = m["arm"]
        sysmsg = SYS_BY_ARM[arm]
        user = f"【材料】\n{m['material']}\n\n【问题】{m['q']}\n\n【回答】"
        txt, u = call(args.base, key, args.model, sysmsg, user)
        return arm, m, txt, u

    jobs = [dict(arm=a, **m) for a in arms for m in items]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        first_out = list(ex.map(first, jobs))
    n1 = len(first_out)
    print(f"首轮完成 {n1}｜{time.time()-t0:.0f}s", flush=True)

    # ---- 需要问的走第二轮：用户回答 → 带对话再答 ----
    def second(t):
        arm, m, txt, _u = t
        qm = ASK_RE.search(txt)
        if arm == "single" or not qm:
            return t, None, None, None
        asked = qm.group(1).strip()[:80]
        vague = arm == "askVague" and (int(hashlib.md5(m["iid"].encode()).hexdigest(), 16) % 100) < 50
        sysu = (SYS_USER_VAGUE if vague else SYS_USER).format(gold=m["gold_facts"] or "（没什么印象）")
        clarify, u2 = call(args.base, key, args.model, sysu,
                           f"助手问：{asked}\n你回答：", max_tokens=40)
        user = (f"【材料】\n{m['material']}\n\n【问题】{m['q']}\n\n"
                f"【刚才的对话】\n助手问：{asked}\n用户答：{clarify}\n\n【回答】")
        txt3, u3 = call(args.base, key, args.model, SYS_FINAL, user)
        return t, asked, (clarify, vague, u2), (txt3, u3)

    need = [t for t in first_out if t[0] != "single" and ASK_RE.search(t[2] or "")]
    print(f"需要追问 {len(need)} 条", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        second_out = list(ex.map(second, need))
    if need:
        print(f"追问完成｜{time.time()-t0:.0f}s", flush=True)
    smap = {(t[0][0], t[0][1]["iid"]): (t[1], t[2], t[3]) for t in second_out}

    rows = []
    stat = defaultdict(Counter)
    for arm, m, txt, u in first_out:
        asked, clar, final = smap.get((arm, m["iid"]), (None, None, None))
        question = asked or ""
        clarify, vague, u2 = ("", False, {})
        if clar:
            clarify, vague, u2 = clar
        ans = clean_ans((final or (txt, u))[0]) if final else clean_ans(txt)
        ab = bool(ABSTAIN.search(ans)) or not ans
        g = norm(m["gold"])
        leak = bool(g) and len(g) >= 2 and (g in norm(clarify))
        part = round(leak or (bool(clarify) and sum(ch in norm(clarify) for ch in set(g)) / max(1, len(set(g))) >= 0.6))
        row = {"arm": arm, "iid": m["iid"], "kind": m["kind"], "q": m["q"], "gold": m["gold"],
               "answer": ans, "abstain": ab, "asked": bool(question), "question": question,
               "clarify": clarify, "vague": vague, "leak": leak, "leakSoft": part,
               "covered": m["covered"], "first": (txt or "")[:200],
               "tokens": (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)
                          + (u2.get("prompt_tokens", 0) + u2.get("completion_tokens", 0))
                          + ((final[1] if final else {}).get("prompt_tokens", 0)
                             + (final[1] if final else {}).get("completion_tokens", 0)))}
        rows.append(row)
        s = stat[arm]
        s["n"] += 1
        s["asked"] += int(row["asked"])
        s["abstain"] += int(ab)
        s["leak"] += int(leak)
        s["uselessAsk"] += int(row["asked"] and m["covered"] < 1.0)
        s["tok"] += row["tokens"]

    json.dump({"model": args.model, "arm": args.arm, "arms": arms, "rows": rows},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n## 问人：首轮行为\n")
    print("| 臂 | 实例 | 提问 | 提问率 | 无效提问（金标不全在材料） | 用户答泄题 | 沉默 |")
    print("|---|---|---|---|---|---|---|")
    for arm in arms:
        c = stat[arm]
        n = c["n"]
        print(f"| {arm} | {n} | {c['asked']} | {100*c['asked']/n:.0f}% | "
              f"{c['uselessAsk']} ({100*c['uselessAsk']/max(1,c['asked']):.0f}%) | "
              f"{c['leak']} ({100*c['leak']/max(1,c['asked']):.0f}%) | {c['abstain']} ({100*c['abstain']/n:.0f}%) |")
    print(f"\n写出 {args.out}")


if __name__ == "__main__":
    main()
