"""用已保存的素材回放当前提示词与模型，输出质量计数，便于改提示词或换模型前后对比。

    python -m newsgen.evaluate 2026-10-13 2026-10-14      # data/digests/ 中已有的期号
    python -m newsgen.evaluate --materials sample.json    # 素材 JSON 列表（每条需有 id）

需要 OPENROUTER_API_KEY；不会写入 data/。数字只反映重试、截断与 token，不判断事实是否准确，
请对照来源抽查打印出的标题。
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
    """返回一行评测结果；生成失败时记录错误而不是抛出。"""
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
