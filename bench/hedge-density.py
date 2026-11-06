"""量一下「不确定词」在这套语料里到底有多少：事实层、金标层、以及那 16 条错答案里各占多少。"""
import json
import os
import re

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(WS, "eventfold", "bench")

HEDGE = re.compile(r"可能|大概|好像|似乎|听说|据说|估计|也许|或许|应该是|说不定|不确定|记不清|"
                   r"印象里|应该是吧|没记错的话|好像是|大概是|应该是|差点|约莫|差不多")

gen = json.load(open(os.path.join(BENCH, "ab-gen-v4.json"), encoding="utf-8"))
qa = json.load(open(os.path.join(WS, "novels", "qa-v2.json"), encoding="utf-8"))["questions"]
wrong = json.load(open(os.path.join(BENCH, "ab-wrong-audit.json"), encoding="utf-8"))

facts = {f["i"]: f["t"] for f in gen["corpus"]}
hedged = {i: t for i, t in facts.items() if HEDGE.search(t)}
print(f"事实层：{len(facts)} 条里带不确定词 {len(hedged)} 条（{100*len(hedged)/len(facts):.1f}%）")
for i, t in list(hedged.items())[:12]:
    m = HEDGE.search(t)
    print(f"  F{i:4d} [{m.group(0)}] {t[:78]}")

gold_hedged = 0
for q in qa:
    g = (q.get("answer") or "") + (q.get("evidence") or "")
    if HEDGE.search(g):
        gold_hedged += 1
print(f"\n金标层：{len(qa)} 题里答案/证据带不确定词 {gold_hedged} 题（{100*gold_hedged/len(qa):.1f}%）")

print(f"\n那 {len(wrong)} 条错答案：")
n_ans = n_gold = 0
for r in wrong:
    a_hedge = HEDGE.search(r.get("answer") or "")
    g_hedge = HEDGE.search(r.get("gold") or "")
    n_ans += bool(a_hedge)
    n_gold += bool(g_hedge)
    print(f"  {r['iid']:14s} ans_hedge={bool(a_hedge)} gold_hedge={bool(g_hedge)} "
          f"| {(r.get('answer') or '')[:24]} vs {(r.get('gold') or '')[:24]}")
print(f"\n错答案里：模型带不确定词 {n_ans}/{len(wrong)}｜金标带不确定词 {n_gold}/{len(wrong)}")

# 语料里同一「槽位」（同一个人 + 同一个量词）出现多个值的次数，看冲突有多密
print("\n带数字的事实里，同一句主语的取值冲突（粗查）：")
by_subj = {}
for i, t in facts.items():
    m = re.search(r"^(老周|李四|王五|张三|肥鱼|张伟|主理人)", t)
    if not m:
        continue
    nums = re.findall(r"\d+(?:\.\d+)?", t)
    by_subj.setdefault(m.group(1), []).append((i, len(nums)))
multi = {k: v for k, v in by_subj.items() if len(v) > 50}
print("  " + "，".join(f"{k} {len(v)} 条" for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))))
