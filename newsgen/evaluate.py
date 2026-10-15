"""Replay saved issues' materials through the current prompts/model and report quality counters.

    python -m newsgen.evaluate 2026-10-13 2026-10-14      # issues stored in data/digests/
    python -m newsgen.evaluate --materials sample.json    # a JSON list of materials (each with an id)

Needs OPENROUTER_API_KEY. Nothing is written to data/. Compare the reports before changing a prompt or model;
the numbers do not judge factual accuracy, so spot-check the printed titles against their sources.
"""
import argparse
import json
import sys

from digest_schema import CATEGORIES
from newsgen import settings
from newsgen.llm import generate_digest
from newsgen.storage import digest_path


def load_materials(date):
    record = json.loads(digest_path(date).read_text(encoding="utf-8"))
    if not record.get("materials"):
        raise SystemExit(f"{date} 是旧版记录，没有保存素材，无法回放")
    return record["materials"]


def evaluate(materials, label, generate=generate_digest):
    """Return one report row; a failed generation is reported instead of raised."""
    run = {}
    row = {"label": label, "materials": len(materials), "model": settings.MODEL}
    try:
        editorial = generate(materials, run)
        row.update(ok=True, items={s["category"]: len(s["items"]) for s in editorial["sections"]},
                   titles=[i["title"] for s in editorial["sections"] for i in s["items"]])
    except Exception as exc:
        row.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    attempts = run.get("attempts", [])
    row.update(requests=len(attempts), retries=sum(1 for a in attempts if a.get("attempt", 1) > 1),
               invalid=sum(1 for a in attempts if a.get("status") != "valid"),
               truncated=sum(1 for a in attempts if a.get("finish_reason") == "length"),
               completion_tokens=sum((a.get("usage") or {}).get("completion_tokens", 0) for a in attempts),
               prompt_tokens=sum((a.get("usage") or {}).get("prompt_tokens", 0) for a in attempts),
               seconds=round(sum(a.get("elapsed_seconds", 0) for a in attempts), 1))
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dates", nargs="*", help="data/digests/ 中的期号")
    parser.add_argument("--materials", help="素材 JSON 文件（列表）")
    args = parser.parse_args(argv)
    settings.validate_config()
    jobs = [(date, load_materials(date)) for date in args.dates]
    if args.materials:
        jobs.append((args.materials, json.load(open(args.materials, encoding="utf-8"))))
    if not jobs:
        parser.error("需要至少一个期号或 --materials")
    rows = [evaluate(materials, label) for label, materials in jobs]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
