#!/usr/bin/env python3
"""Build `archive.json` (the only file the runtime needs) from a plain fact stream.

Input: JSONL, one fact per line::

    {"id": 1, "text": "...", "ts": "2026-03-02 10:00",
     "thread": "运维", "schema": "incident-online", "subject": "网关缓存",
     "slots": [{"n": "现象", "v": "大量 502"}], "closed": false}

Only `text` and `ts` are required. `thread` / `schema` / `subject` / `slots` /
`closed` are optional; missing ones fall back to heuristics (see below).

What it does, in order:

1. **group** facts into candidate events — same `schema` + same `subject`,
   within `--guard-days`, not yet `closed`, and under `--max-facts`;
2. **fold** slot names onto a closed vocabulary (`--mapping`), long tail goes
   into a single "备注" bucket instead of inventing new keys;
3. **split** catch-all threads (default 其他/未分类) by date gaps, because a
   300-fact bucket can only ever show its first few facts;
4. emit `archive.json`.

Pure standard library. No model calls, no network.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
CATCHALL_DEFAULT = "其他,未分类"


# ---------------------------------------------------------------- helpers
def bigrams(s: str) -> frozenset[str]:
    s = "".join(ch for ch in str(s) if ch.isalnum() or CJK.match(ch))
    return frozenset(s[i : i + 2] for i in range(len(s) - 1)) or frozenset([s])


def jaccard(a: str, b: str) -> float:
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def day(s: str) -> date | None:
    try:
        y, m, d = (s or "")[:10].split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def known(schema: str, facts_by_schema: dict[str, set[str]]) -> set[str]:
    return facts_by_schema.get(schema) or set()


def same_subject(a: str, b: str) -> bool:
    if a == b:
        return True
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return False
    if ba <= bb or bb <= ba:  # "缓存 TTL" vs "缓存 TTL 配置"
        return True
    return len(ba & bb) / len(ba | bb) >= 0.5


# ---------------------------------------------------------------- loading
def load_facts(path: Path) -> list[dict]:
    facts = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{lineno}: bad JSON ({e})")
        text = str(rec.get("text") or "").strip()
        if not text:
            continue
        ts = str(rec.get("ts") or rec.get("date") or "")[:19]
        slots = rec.get("slots") or []
        if isinstance(slots, dict):
            slots = [{"n": k, "v": v} for k, v in slots.items()]
        slots = [{"n": str(s.get("n", "")).strip(), "v": str(s.get("v", "")).strip()} for s in slots]
        facts.append(
            {
                "id": rec.get("id", lineno),
                "text": text,
                "ts": ts,
                "d": ts[:10],
                "thread": str(rec.get("thread") or rec.get("topic") or "").strip(),
                "schema": str(rec.get("schema") or rec.get("kind") or "general").strip(),
                "subject": str(rec.get("subject") or rec.get("subj") or "").strip(),
                "slots": [s for s in slots if s["n"] and s["v"]],
                "closed": bool(rec.get("closed")),
            }
        )
    return facts


# ---------------------------------------------------------------- grouping
def derive_subject(f: dict) -> str:
    """What this fact is 'about': explicit field, else a slot, else first words."""
    if f["subject"]:
        return f["subject"]
    for key in ("主体", "对象", "物品", "参数名", "现象", "项目", "主题"):
        for s in f["slots"]:
            if s["n"] == key and s["v"]:
                return s["v"]
    head = re.split(r"[，。；、,.;]|(?<=[\u4e00-\u9fff]) (?=[A-Za-z])", f["text"])[0]
    return head[:24].strip() or f["text"][:24]


def group_events(facts: list[dict], guard_days: int, max_facts: int) -> list[dict]:
    """Walk facts in time order and start a new event when the thread of thought breaks."""
    facts = sorted(facts, key=lambda f: (f["ts"], str(f["id"])))
    events: list[dict] = []
    cur: dict | None = None
    for f in facts:
        subj = derive_subject(f)
        d = day(f["ts"])
        join = False
        if cur is not None:
            same_kind = cur["schema"] == f["schema"] and cur["thread"] == f["thread"]
            close_enough = (
                d is None or cur["_last_day"] is None or abs((d - cur["_last_day"]).days) <= guard_days
            )
            join = (
                same_kind
                and close_enough
                and not cur["closed"]
                and same_subject(cur["_subject"], subj)
                and len(cur["facts"]) < max_facts
            )
        if not join:
            cur = {
                "id": "",  # filled later
                "schema": f["schema"],
                "thread": f["thread"],
                "title": f"{f['thread']}：{subj}" if f["thread"] else subj,
                "status": "open",
                "first": f["d"],
                "last": f["d"],
                "summary": "",
                "slots": [],
                "facts": [],
                "_subject": subj,
                "_last_day": d,
                "closed": False,
            }
            events.append(cur)
        cur["facts"].append({"i": f["id"], "d": f["d"], "t": f["text"]})
        cur["first"] = min(cur["first"], f["d"]) if cur["first"] else f["d"]
        cur["last"] = max(cur["last"], f["d"]) if cur["last"] else f["d"]
        cur["_last_day"] = d or cur["_last_day"]
        cur["closed"] = cur["closed"] or f["closed"]
        seen = {(s["n"], s["v"]) for s in cur["slots"]}
        for s in f["slots"]:
            if (s["n"], s["v"]) not in seen:
                cur["slots"].append(dict(s))
                seen.add((s["n"], s["v"]))
    for i, e in enumerate(events, 1):
        e["id"] = f"ev-{i:04d}"
        e["n"] = len(e["facts"])
        e["status"] = "closed" if e["closed"] else "open"
        joined = "；".join(x["t"] for x in e["facts"][:3])
        e["summary"] = joined[:160] + ("…" if len(joined) > 160 else "")
        if not e["first"]:
            e["first"] = e["facts"][0]["d"]
        e.pop("_subject", None)
        e.pop("_last_day", None)
        e.pop("closed", None)
    events.sort(key=lambda e: (e["first"], e["id"]))
    return events


# ---------------------------------------------------------------- slots
def fold_slots(events: list[dict], mapping: dict | None) -> tuple[list[dict], dict]:
    """Map free-form slot names onto a closed vocabulary; unknown names go to one bucket."""
    if not mapping:
        return events, {"folded": 0, "misc": 0}
    canon = mapping.get("canonical") or {}
    aliases = {str(k): str(v) for k, v in (mapping.get("aliases") or {}).items()}
    misc_key = mapping.get("misc_key") or "备注"
    folded = misc = 0

    def pick(name: str, schema: str) -> str | None:
        allowed = set(canon.get(schema) or [])
        if not allowed:
            return name
        if name in allowed:
            return name
        if aliases.get(name) in allowed:
            return aliases[name]
        best, best_s = None, 0.0
        for c in allowed:
            s = jaccard(name, c)
            if s > best_s:
                best, best_s = c, s
        return best if best_s >= 0.34 else None

    out = []
    for e in events:
        buckets: dict[tuple[str, str], dict] = {}
        tail: list[str] = []
        for s in e["slots"]:
            target = pick(s["n"], e["schema"])
            if target is None:
                misc += 1
                tail.append(f"{s['n']}={s['v']}")
                continue
            if target != s["n"]:
                folded += 1
            key = (target, s.get("span") or "")
            if key in buckets:
                if s["v"] not in buckets[key]["v"]:
                    buckets[key]["v"] += " / " + s["v"]
            else:
                item = {"n": target, "v": s["v"]}
                if s.get("span"):
                    item["span"] = s["span"]
                buckets[key] = item
        e = dict(e)
        e["slots"] = list(buckets.values())
        if tail:
            e["slots"].append({"n": misc_key, "v": "；".join(tail[:4])[:90]})
        out.append(e)
    return out, {"folded": folded, "misc": misc}


# ---------------------------------------------------------------- catch-all
def split_catchall(events: list[dict], names: set[str], max_facts: int, gap: int) -> tuple[list[dict], int]:
    """Break a 300-fact '其他' bucket apart at date cliffs."""
    out, split = [], 0
    for e in events:
        if e["thread"] not in names or len(e["facts"]) <= max_facts:
            out.append(e)
            continue
        groups, cur = [], []
        for f in sorted(e["facts"], key=lambda x: (x["d"], str(x["i"]))):
            if cur:
                a, b = day(cur[-1]["d"]), day(f["d"])
                if a and b and (b - a).days > gap:
                    groups.append(cur)
                    cur = []
            cur.append(f)
        if cur:
            groups.append(cur)
        if len(groups) <= 1:
            out.append(e)
            continue
        split += 1
        for gi, g in enumerate(groups, 1):
            ne = dict(e)
            ne["id"] = f"{e['id']}-p{gi}"
            ne["facts"] = g
            ne["n"] = len(g)
            ne["first"], ne["last"] = g[0]["d"], g[-1]["d"]
            ne["slots"] = list(e["slots"]) if gi == 1 else []
            out.append(ne)
    return out, split


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="facts.jsonl -> archive.json")
    ap.add_argument("--facts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mapping", default="", help="optional slot vocabulary JSON")
    ap.add_argument("--catchall", default=CATCHALL_DEFAULT)
    ap.add_argument("--guard-days", type=int, default=14)
    ap.add_argument("--max-facts", type=int, default=24)
    ap.add_argument("--split-max", type=int, default=18)
    ap.add_argument("--split-gap", type=int, default=2)
    ap.add_argument("--built", default="", help="value for the built field")
    args = ap.parse_args()

    facts = load_facts(Path(args.facts))
    if not facts:
        raise SystemExit("no facts loaded")

    mapping = None
    if args.mapping:
        mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))

    events = group_events(facts, args.guard_days, args.max_facts)
    print(f"group: {len(facts)} facts -> {len(events)} candidate events")

    if mapping:
        events, st = fold_slots(events, mapping)
        print(f"fold: renamed {st['folded']} slot(s), {st['misc']} pushed into the misc bucket")

    names = {s.strip() for s in args.catchall.split(",") if s.strip()}
    events, split = split_catchall(events, names, args.split_max, args.split_gap)
    if split:
        print(f"split: broke {split} catch-all event(s) apart by date, {len(events)} events now")

    doc = {
        "version": 1,
        "built": args.built or date.today().isoformat(),
        "note": "event archive export: the runtime only reads this file",
        "events": events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_slots = sum(len(e["slots"]) for e in events)
    print(f"wrote {out}: {len(events)} events, {len(facts)} facts, {n_slots} slots, {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
