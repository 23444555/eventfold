#!/usr/bin/env python3
"""Builder tests: run the CLI on the bundled example and assert the shape.

    python test/build_test.py

No dependencies beyond the standard library.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / "build" / "archive_build.py"
EX_FACTS = ROOT / "examples" / "facts.jsonl"
EX_ARCHIVE = ROOT / "examples" / "data" / "archive.json"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(("  ok   " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def run(*args: str) -> str:
    p = subprocess.run([sys.executable, str(BUILD), *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"build failed:\n{p.stdout}\n{p.stderr}")
    return p.stdout


def main() -> None:
    print("example archive")
    doc = json.loads(EX_ARCHIVE.read_text(encoding="utf-8"))
    events = doc["events"]
    check("version is 1", doc.get("version") == 1)
    check("has events", len(events) >= 10, f"{len(events)}")
    ids = [e["id"] for e in events]
    check("event ids are unique", len(ids) == len(set(ids)))

    facts_total = 0
    seen: set = set()
    for e in events:
        for key in ("id", "schema", "thread", "title", "status", "first", "last", "n", "summary", "slots", "facts"):
            if key not in e:
                check(f"{e.get('id')} has {key}", False)
                break
        facts_total += len(e["facts"])
        check(f"{e['id']} n matches fact count", e["n"] == len(e["facts"]), f"{e['n']} vs {len(e['facts'])}")
        check(f"{e['id']} dates ordered", e["first"] <= e["last"], f"{e['first']} > {e['last']}")
        for f in e["facts"]:
            seen.add(f["i"])
    check("every fact landed exactly once", facts_total == len(seen) == 33, f"{facts_total}/{len(seen)}")
    check("no event exceeds 24 facts", max(e["n"] for e in events) <= 24)

    print("catch-all split")
    with tempfile.TemporaryDirectory() as tmp:
        facts = Path(tmp) / "big.jsonl"
        rows = []
        for i in range(40):  # 20 facts far apart in time inside a catch-all thread
            day = 1 + i * 3
            rows.append(
                json.dumps(
                    {"id": i, "ts": f"2026-01-{day:02d} 10:00" if day <= 28 else f"2026-02-{day - 28:02d} 10:00",
                     "thread": "其他", "schema": "note", "subject": "杂项", "text": f"杂项记录 {i}"},
                    ensure_ascii=False,
                )
            )
        facts.write_text("\n".join(rows), encoding="utf-8")
        out = Path(tmp) / "a.json"
        run("--facts", str(facts), "--out", str(out), "--catchall", "其他", "--split-max", "18", "--split-gap", "2")
        got = json.loads(out.read_text(encoding="utf-8"))["events"]
        check("catch-all was split", len(got) > 1, f"{len(got)} events")
        check("split parts keep the facts", sum(e["n"] for e in got) == 40)

    print("slot folding")
    with tempfile.TemporaryDirectory() as tmp:
        mapping = Path(tmp) / "mapping.json"
        mapping.write_text(
            json.dumps(
                {
                    "canonical": {"incident-online": ["现象", "原因", "修复", "备注"]},
                    "aliases": {"故障": "现象"},
                    "misc_key": "备注",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out = Path(tmp) / "a.json"
        run("--facts", str(EX_FACTS), "--out", str(out), "--mapping", str(mapping))
        events2 = json.loads(out.read_text(encoding="utf-8"))["events"]
        allowed = {"现象", "原因", "修复", "备注"}
        bad = []
        for e in events2:
            if e["schema"] != "incident-online":
                continue
            for s in e["slots"]:
                if s["n"] not in allowed:
                    bad.append(s["n"])
        check("incident slots fold onto the vocabulary", not bad, ",".join(bad))

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
