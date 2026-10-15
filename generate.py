import argparse
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from digest_schema import CATEGORIES, PROMPT_VERSION, SCHEMA_VERSION, render_editorial
from newsgen import settings
from newsgen.feeds import collect_articles
from newsgen.llm import generate_digest
from newsgen.settings import validate_config
from newsgen.storage import atomic_json, issue_exists, save_record

ROOT = settings.ROOT
DATA_DIR = ROOT / "data"
RUN_DIR = ROOT / ".runs"


def issue_window(issue_date=None, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(settings.CHINA)
    hour, minute = map(int, settings.CONFIG["issue_cutoff"].split(":"))
    if issue_date:
        day = datetime.strptime(issue_date, "%Y-%m-%d")
        if day.strftime("%Y-%m-%d") != issue_date:
            raise ValueError("日期格式必须是 YYYY-MM-DD")
        end = day.replace(hour=hour, minute=minute, tzinfo=settings.CHINA)
    else:
        end = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if end > local:
            end -= timedelta(days=1)
    end = end.astimezone(timezone.utc)
    if end > now:
        raise ValueError("不能生成尚未结束的采集窗口")
    return end.astimezone(settings.CHINA).strftime("%Y-%m-%d"), end - timedelta(hours=settings.LOOKBACK_HOURS), end


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成固定北京时间窗口的新闻简报")
    parser.add_argument("--check-feeds", action="store_true", help="只检查来源，不调用 AI")
    parser.add_argument("--force", action="store_true", help="该期已存在时仍重新生成并替换")
    parser.add_argument("--date", help="期号日期 YYYY-MM-DD，窗口截至当日北京时间 08:17；默认最近结束的窗口")
    args = parser.parse_args(argv)
    validate_config()
    today, start, end = issue_window(args.date)
    if args.check_feeds:
        _, issues = collect_articles(start, end)
        for issue in issues:
            print(f"[{issue['category']}] {issue['name']} | {issue['url']} | {issue['reason']}")
        return 1 if issues else 0

    if not args.force and issue_exists(today):
        print(f"{today} 期已存在，跳过生成（使用 --force 可重新生成并替换）")
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    journal = RUN_DIR / f"{run_id}.json"
    run = {
        "run_id": run_id, "issue_date": today, "status": "collecting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "prompt_version": PROMPT_VERSION, "model": settings.MODEL,
        "config": {**settings.CONFIG, "lookback_hours": settings.LOOKBACK_HOURS, "max_articles": settings.MAX_ARTICLES,
                   "max_per_source": settings.MAX_PER_SOURCE, "min_items_per_category": settings.MIN_ITEMS_PER_CATEGORY,
                   "feed_timeout": settings.FEED_TIMEOUT},
    }
    checkpoint = lambda: atomic_json(journal, run)
    checkpoint()
    try:
        print(f"Collecting RSS for {today}: [{start.isoformat()}, {end.isoformat()})")
        articles, issues = collect_articles(start, end)
        run.update(materials=articles, feed_issues=issues, category_counts=dict(Counter(a["category"] for a in articles)))
        checkpoint()
        short = {category: run["category_counts"].get(category, 0) for category in CATEGORIES
                 if run["category_counts"].get(category, 0) < settings.MIN_ITEMS_PER_CATEGORY}
        if short:
            raise RuntimeError(f"栏目素材不足，保留原归档且不调用 AI：{short}")
        run["status"] = "generating"
        editorial = generate_digest(articles, run, checkpoint)
        now = datetime.now(timezone.utc).isoformat()
        run.update(status="validated", completed_at=now)
        evidence_path = DATA_DIR / "runs" / today / f"{run_id}.json"
        atomic_json(evidence_path, run)
        record = {
            "schema_version": SCHEMA_VERSION, "date": today,
            "title": f"每日新闻总结 | {today}", "guid": f"daily-news-{today}",
            "created_at": now, "content": render_editorial(editorial, articles),
            "editorial": editorial, "materials": articles,
            "article_count": len(articles), "feed_issues": issues,
            "generation": {
                "run_id": run_id, "model": settings.MODEL, "prompt_version": PROMPT_VERSION,
                "strategy": run["strategy"],
                "prompt_sha256": run["prompt_sha256"], "window_start": start.isoformat(),
                "window_end": end.isoformat(), "minimum_items": settings.MIN_ITEMS_PER_CATEGORY,
                "evidence_path": str(evidence_path.relative_to(ROOT)),
            },
        }
        save_record(record)
        run["status"] = "saved"
        print(f"Saved {today}; all historical issues retained. Run npm run build to validate the website.")
    except Exception as exc:
        run.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, (ValueError, RuntimeError)):
            run["error"] = str(exc)
        raise
    finally:
        checkpoint()


if __name__ == "__main__":
    sys.exit(main())
