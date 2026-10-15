"""命令行入口：确定采集窗口 → 生成最新一期 → 补齐缺失期号。

用法：python3 generate.py [--date YYYY-MM-DD] [--force] [--no-backfill] [--check-feeds] [--check-gaps]
具体逻辑在 newsgen/ 包：settings 配置、feeds 采集、prompts 提示词、llm 模型调用、storage 归档。
"""
import argparse
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from digest_schema import CATEGORIES, PROMPT_VERSION, SCHEMA_VERSION, render_editorial
from newsgen import settings
from newsgen.feeds import collect_articles
from newsgen.llm import generate_digest
from newsgen.settings import validate_config
from newsgen.storage import archived_dates, atomic_json, issue_exists, save_record

ROOT = settings.ROOT
DATA_DIR = ROOT / "data"      # 成功生成的证据写入 data/runs/，随新闻一起提交
RUN_DIR = ROOT / ".runs"       # 生成过程的检查点，失败时用于排查，不提交到 Git


def issue_window(issue_date=None, now=None):
    """返回 (期号日期, 窗口起点, 窗口终点)。

    窗口固定为北京时间 issue_cutoff 之前的 lookback_hours 小时，左闭右开。不指定日期时取最近一个
    已结束的窗口，因此任务延迟不会移动同一期的边界；尚未结束的窗口会被拒绝。
    """
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


def missing_issue_dates(target_date, days=None, archived=None):
    """返回 target_date 之前 days 天内尚未归档的期号，从旧到新排列。

    不会早于第一期存档，也不会回溯超过 days 天：RSS 只保留较新的条目，更早的窗口无法重建。
    """
    days = settings.CONFIG["backfill_days"] if days is None else days
    archived = archived_dates() if archived is None else archived
    if not archived:
        return []
    target = datetime.strptime(target_date, "%Y-%m-%d")
    wanted = [(target - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days, 0, -1)]
    return [date for date in wanted if date >= archived[0] and date not in archived]


def generate_issue(today, start, end, backfill=False):
    """采集、生成、校验并保存一期。任何一步失败都会抛出异常，且不改动已有归档。

    backfill=True 表示这是补齐的期号，记录与证据里会带 backfilled 标记。
    """
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    journal = RUN_DIR / f"{run_id}.json"
    run = {
        "run_id": run_id, "issue_date": today, "status": "collecting", "backfill": backfill,
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
        # 1. 采集：固定窗口内的素材；任一栏素材不足时不调用模型
        print(f"Collecting RSS for {today}: [{start.isoformat()}, {end.isoformat()})")
        articles, issues = collect_articles(start, end)
        run.update(materials=articles, feed_issues=issues, category_counts=dict(Counter(a["category"] for a in articles)))
        checkpoint()
        short = {category: run["category_counts"].get(category, 0) for category in CATEGORIES
                 if run["category_counts"].get(category, 0) < settings.MIN_ITEMS_PER_CATEGORY}
        if short:
            raise RuntimeError(f"栏目素材不足，保留原归档且不调用 AI：{short}")
        # 2. 生成：六栏逐一生成并校验，再生成概览
        run["status"] = "generating"
        editorial = generate_digest(articles, run, checkpoint)
        now = datetime.now(timezone.utc).isoformat()
        run.update(status="validated", completed_at=now)
        # 3. 保存：先写证据（素材、提示词、原始输出），再写当期记录；构建时会核对两者一致
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
                **({"backfilled": True} if backfill else {}),
            },
        }
        save_record(record)
        run["status"] = "saved"
        print(f"Saved {today}{' (补齐）' if backfill else ''}; all historical issues retained. Run npm run build to validate the website.")
    except Exception as exc:
        run.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, (ValueError, RuntimeError)):
            run["error"] = str(exc)
        raise
    finally:
        checkpoint()


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成固定北京时间窗口的新闻简报")
    parser.add_argument("--check-feeds", action="store_true", help="只检查来源，不调用 AI")
    parser.add_argument("--check-gaps", action="store_true", help="只列出缺失的期号，有缺失则退出码为 1")
    parser.add_argument("--no-backfill", action="store_true", help="不补齐缺失的期号")
    parser.add_argument("--force", action="store_true", help="该期已存在时仍重新生成并替换")
    parser.add_argument("--date", help="期号日期 YYYY-MM-DD，窗口截至当日北京时间 08:17；默认最近结束的窗口。指定日期时只生成该期")
    args = parser.parse_args(argv)
    validate_config()
    started = time.monotonic()
    today, start, end = issue_window(args.date)
    if args.check_feeds:
        _, issues = collect_articles(start, end)
        for issue in issues:
            print(f"[{issue['category']}] {issue['name']} | {issue['url']} | {issue['reason']}")
        return 1 if issues else 0
    if args.check_gaps:
        gaps = missing_issue_dates(today)
        for date in gaps:
            print(f"缺失期号：{date}")
        return 1 if gaps else 0

    # 最新一期优先，失败即整体失败；补齐不会危及它。
    if not args.force and issue_exists(today):
        print(f"{today} 期已存在，跳过生成（使用 --force 可重新生成并替换）")
    else:
        generate_issue(today, start, end)

    if args.date or args.no_backfill:
        return 0
    failed = []
    for date in missing_issue_dates(today):
        # 限制总耗时：避免补齐拖太久，导致整个步骤超时被杀、已成功的最新一期也丢失。
        if time.monotonic() - started > settings.CONFIG["backfill_start_deadline_seconds"]:
            failed.append(date)
            print(f"::warning::已超过补齐开工截止时间，未尝试 {date}")
            continue
        print(f"检测到缺失期号 {date}，开始补齐（按当前 RSS 重建该窗口，较早内容可能已被上游移除）")
        try:
            generate_issue(*issue_window(date), backfill=True)
        except Exception as exc:
            failed.append(date)
            print(f"::warning::补齐 {date} 失败：{type(exc).__name__}: {exc}")
    if failed:
        print(f"仍缺失：{', '.join(failed)}。超过 {settings.CONFIG['backfill_days']} 天后将不再尝试，需要时可用 --date 手动补跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
