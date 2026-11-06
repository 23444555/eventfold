import json
import sys

p = sys.argv[1] if len(sys.argv) > 1 else r"eventfold\bench\_smoke-ask.json"
d = json.load(open(p, encoding="utf-8"))
for r in d["rows"]:
    print(r["arm"], "|", r["q"][:44], "| first:", (r["first"] or "")[:80].replace("\n", " / "),
          "| ans:", r["answer"][:40], "| cov:%.1f" % r["covered"])
    if r["asked"]:
        print("    ASK:", r["question"], "| user:", r["clarify"], "| vague:", r["vague"], "| leak:", r["leak"])
