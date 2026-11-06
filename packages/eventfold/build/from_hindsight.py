#!/usr/bin/env python3
"""Pull a fact stream out of a Hindsight bank and write eventfold's JSONL.

Hindsight stores facts, not episodes. This adapter is the "from" side the README
says you have to write yourself:

    python build/from_hindsight.py --bank qq-live --out facts.jsonl
    python build/archive_build.py --facts facts.jsonl --out data/archive.json

Mapping (deliberately dumb — the builder does the folding, not this script):

    Hindsight item            -> eventfold fact
    text                       -> text        (required)
    occurred_start | mentioned_at | date -> ts (required)
    fact_type                  -> schema
    tags[0] | context          -> thread
    entities[0]                -> subject
    —                          -> slots []    (facts are prose; no slot extraction here)

Pure standard library, read-only against the API. No model calls.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAGE = 200
YEAR = 0
LIFT_TS = False
SUBJECT_FROM = "entities"
THREAD_FROM = "context"


def fetch(base: str, bank: str, offset: int, limit: int) -> dict:
    url = f"{base.rstrip('/')}/banks/{urllib.parse.quote(bank)}/memories/list?limit={limit}&offset={offset}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def ts_of(item: dict) -> str:
    for key in ("occurred_start", "mentioned_at", "date"):
        raw = item.get(key)
        if not raw:
            continue
        s = str(raw).replace("T", " ").replace("Z", "")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return ""


TS_IN_TEXT = re.compile(r"^\[(\d{2})-(\d{2}) (\d{2}):(\d{2})\]")
SPEAKER = re.compile(r"^\[[^\]]+\]\s*([^：:\n]{1,12})[：:]")


def ts_from_text(text: str, year: int) -> str:
    m = TS_IN_TEXT.match(text)
    if not m:
        return ""
    mm, dd, hh, mi = (int(x) for x in m.groups())
    return f"{year:04d}-{mm:02d}-{dd:02d} {hh:02d}:{mi:02d}"


def to_fact(item: dict, idx: int) -> dict | None:
    text = (item.get("text") or "").strip()
    ts = (ts_from_text(text, YEAR) if LIFT_TS else "") or ts_of(item)
    if not text or not ts:
        return None
    tags = item.get("tags") or []
    entities = item.get("entities") or []

    subject = ""
    if SUBJECT_FROM == "entities":
        for e in entities:
            if isinstance(e, str) and e.strip():
                subject = e.strip()
                break
            if isinstance(e, dict) and e.get("name"):
                subject = str(e["name"]).strip()
                break
    elif SUBJECT_FROM == "thread":
        subject = str(item.get("context") or "")[:40] or str((tags or [""])[0])[:40]
    elif SUBJECT_FROM == "speaker":
        first = text.split("\n", 1)[0]
        m = SPEAKER.match(first)
        if m:
            subject = m.group(1).strip()

    thread = ""
    if THREAD_FROM == "none":
        thread = ""
    elif THREAD_FROM == "context":
        thread = str(item.get("context") or "")[:40]
        if not thread and isinstance(tags, list) and tags:
            thread = str(tags[0])[:40]
    elif THREAD_FROM == "tags":
        if isinstance(tags, list) and tags:
            thread = str(tags[0])[:40]
    else:  # document
        doc = str(item.get("document_id") or "")
        thread = doc[:8]
    return {
        "id": idx,
        "text": text,
        "ts": ts,
        "schema": str(item.get("fact_type") or ""),
        "thread": thread,
        "subject": subject,
        "slots": [],
        "closed": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Hindsight bank -> eventfold facts JSONL")
    ap.add_argument("--base", default="http://127.0.0.1:8888/v1/default")
    ap.add_argument("--bank", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="stop after N facts (0 = all)")
    ap.add_argument("--fact-types", default="", help="comma list, e.g. world,experience")
    ap.add_argument("--exclude-doc-prefix", default="", help="drop facts whose document_id starts with this")
    ap.add_argument("--drop-injected", action="store_true", default=True,
                    help="drop facts that are prompt scaffolding, not conversation (default on)")
    ap.add_argument("--thread-from", choices=["context", "tags", "document", "none"], default="context",
                    help="where the thread bucket comes from (default: context, then tags)")
    ap.add_argument("--subject-from", choices=["entities", "speaker", "thread", "none"], default="entities",
                    help="where the subject comes from (default: first entity)")
    ap.add_argument("--ts-from-text", action="store_true",
                    help="lift the timestamp out of the text (chat logs carry [MM-DD HH:MM])")
    ap.add_argument("--year", type=int, default=0, help="year for --ts-from-text (default: this year)")
    args = ap.parse_args()

    global YEAR, LIFT_TS, SUBJECT_FROM, THREAD_FROM
    YEAR = args.year or datetime.now().year
    LIFT_TS = bool(args.ts_from_text)
    SUBJECT_FROM = args.subject_from
    THREAD_FROM = args.thread_from
    wanted = {t.strip() for t in args.fact_types.split(",") if t.strip()}
    rows, offset, total = [], 0, None
    while True:
        page = fetch(args.base, args.bank, offset, PAGE)
        total = page.get("total", 0)
        items = page.get("items") or []
        if not items:
            break
        for item in items:
            if wanted and item.get("fact_type") not in wanted:
                continue
            if args.exclude_doc_prefix and str(item.get("document_id") or "").startswith(args.exclude_doc_prefix):
                continue
            if args.drop_injected:
                head = str(item.get("text") or "")[:40]
                if head.startswith(("【记忆】", "【本次消息快照】", "Time sampled", "以上消息已提供")):
                    continue
            fact = to_fact(item, len(rows) + 1)
            if fact:
                rows.append(fact)
        offset += len(items)
        if offset >= total or (args.limit and len(rows) >= args.limit):
            break
    if args.limit:
        rows = rows[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for fact in rows:
            f.write(json.dumps(fact, ensure_ascii=False) + "\n")

    schemas, threads = {}, {}
    for f in rows:
        schemas[f["schema"]] = schemas.get(f["schema"], 0) + 1
        threads[f["thread"]] = threads.get(f["thread"], 0) + 1
    print(f"{args.bank}: {len(rows)} facts / total {total} -> {out}", file=sys.stderr)
    print("  schema:", ", ".join(f"{k or '(none)'}={v}" for k, v in sorted(schemas.items(), key=lambda kv: -kv[1])[:6]), file=sys.stderr)
    print("  thread:", ", ".join(f"{k or '(none)'}={v}" for k, v in sorted(threads.items(), key=lambda kv: -kv[1])[:6]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
