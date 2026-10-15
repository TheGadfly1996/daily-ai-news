"""RSS 来源的读取、抓取、挑选，以及按窗口采集素材。"""
import calendar
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import feedparser

from digest_schema import CATEGORIES, material_id, normalized_title, safe_url
from newsgen import settings


def load_feeds():
    """读取 feeds.txt（格式：栏目 | 名称 | 链接，# 开头为注释），同栏目内相同链接只保留一个。"""
    feeds = []
    seen = set()
    for line_number, line in enumerate(settings.FEEDS_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3 or parts[0] not in CATEGORIES or not parts[1] or not safe_url(parts[2]):
            raise ValueError(f"feeds.txt 第 {line_number} 行格式错误")
        category, name, url = parts
        if (category, url) not in seen:
            feeds.append({"category": category, "name": name, "url": url})
            seen.add((category, url))
    return feeds


def clean_text(value):
    """去掉 HTML 标签与实体，压缩空白，得到纯文本摘要。"""
    if not value:
        return ""

    value = re.sub(r"<[^>]+>", " ", str(value))
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def entry_datetime(entry):
    """条目的发布（或更新）时间，统一为 UTC；没有时间的条目返回 None，会被丢弃。"""
    for key in ("published_parsed", "updated_parsed"):

        value = entry.get(key)

        if value:
            timestamp = calendar.timegm(value)
            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

    return None


def fetch_feed(feed_config, attempts=2, pause=1.0):
    """抓取来源；网络错误、超时、HTTP 429/5xx 视为暂时故障，重试一次；403/404 与内容无效不重试。"""
    for attempt in range(attempts):
        feed = _fetch_feed_once(feed_config)
        if not feed.error or attempt == attempts - 1 or not feed.transient:
            return feed
        time.sleep(pause)
    return feed


def _fetch_feed_once(feed_config):
    """抓取一次，支持 RSS/Atom/JSON Feed；失败不抛异常，而是返回带 error 与 transient 的结果。"""
    url = feed_config["url"]
    transient = False
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 Daily-AI-News-RSS/1.0"})
        with urlopen(request, timeout=settings.FEED_TIMEOUT) as response:
            payload = response.read(5_000_001)
            content_type = response.headers.get("Content-Type", "")
        if len(payload) > 5_000_000:
            raise ValueError("响应超过 5 MB")
        if "json" in content_type or url.endswith(".json"):
            data = json.loads(payload)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("JSON 响应不是有效的 Feed")
            entries = []
            for item in data.get("items", []):
                if not isinstance(item, dict):
                    continue
                date = item.get("date_published") or item.get("date_modified")
                if not date:
                    continue
                try:
                    published = datetime.fromisoformat(date.replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                entries.append({
                    "title": item.get("title", ""),
                    "link": item.get("url") or item.get("external_url") or "",
                    "summary": item.get("summary") or item.get("content_text") or item.get("content_html") or "",
                    "published_parsed": time.gmtime(published.timestamp()),
                })
            return SimpleNamespace(entries=entries, error=None, transient=False)
        feed = feedparser.parse(payload)
        if not feed.version or (feed.bozo and not feed.entries):
            reason = str(feed.bozo_exception) if feed.bozo else "响应不是 RSS 或 Atom Feed"
            raise ValueError(f"RSS 解析失败：{reason}")
        return SimpleNamespace(entries=feed.entries, error=None, transient=False)
    except HTTPError as exc:
        reason = f"HTTP {exc.code}"
        transient = exc.code == 429 or exc.code >= 500
        exc.close()
    except URLError as exc:
        reason = f"连接失败：{exc.reason}"
        transient = True
    except TimeoutError as exc:
        reason = f"{type(exc).__name__}：{exc}"
        transient = True
    except Exception as exc:
        reason = f"{type(exc).__name__}：{exc}"
    print(f"Feed failed: {url}: {reason}")
    return SimpleNamespace(entries=[], error=reason[:200], transient=transient)


def select_articles(articles):
    """挑选送给模型的素材：先保证每个栏目、每个来源都有名额，再按时间从新到旧补满。"""
    if settings.MAX_ARTICLES <= 0 or settings.MAX_PER_SOURCE <= 0:
        return []
    ordered = sorted(articles, key=lambda item: item["published"], reverse=True)
    selected = []
    selected_ids = set()
    source_counts = {}
    quota = settings.MAX_ARTICLES // len(CATEGORIES)

    def add(item):
        key = (item["category"], item["source"])
        if id(item) in selected_ids or source_counts.get(key, 0) >= settings.MAX_PER_SOURCE:
            return False
        selected.append(item)
        selected_ids.add(id(item))
        source_counts[key] = source_counts.get(key, 0) + 1
        return True

    for category in CATEGORIES:
        matches = [item for item in ordered if item["category"] == category]
        sources = list(dict.fromkeys(item["source"] for item in matches))
        while len([item for item in selected if item["category"] == category]) < quota:
            added = False
            for source in sources:
                match = next((item for item in matches if item["source"] == source and id(item) not in selected_ids), None)
                if match and add(match):
                    added = True
                if len([item for item in selected if item["category"] == category]) >= quota:
                    break
            if not added:
                break
    for item in ordered:
        if len(selected) >= settings.MAX_ARTICLES:
            break
        add(item)
    return selected


def collect_articles(window_start=None, window_end=None):
    """并发抓取所有来源，保留窗口 [起点, 终点) 内的条目，去重后挑选素材。

    返回 (素材列表, 无法读取的来源列表)。
    """
    now = window_end or datetime.now(timezone.utc)
    cutoff = window_start or now - timedelta(hours=settings.LOOKBACK_HOURS)
    fetched_at = datetime.now(timezone.utc).isoformat()
    articles = []
    seen_links = set()
    seen_titles = set()
    feed_issues = []

    feed_configs = load_feeds()
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = pool.map(fetch_feed, feed_configs)
        for feed_config, feed in zip(feed_configs, fetched):
            url = feed_config["url"]
            category = feed_config["category"]
            if feed.error:
                feed_issues.append({
                    "category": category,
                    "name": feed_config["name"],
                    "url": url,
                    "reason": feed.error,
                })
                continue

            recent_count = 0
            missing_date_count = 0

            for entry in feed.entries:
                published = entry_datetime(entry)
                if published is None:
                    missing_date_count += 1
                    continue
                if published < cutoff or published >= now:
                    continue
                recent_count += 1
                title = clean_text(entry.get("title", ""))
                link = entry.get("link") or entry.get("guid") or ""
                link = link if safe_url(link) else ""
                if not title:
                    continue
                normalized = normalized_title(title)
                # 链接跨栏目唯一（feeds.txt 中靠前的来源优先）；标题只在同一栏目内去重
                key_title = (category, normalized)
                if (link and link in seen_links) or key_title in seen_titles:
                    continue
                if link:
                    seen_links.add(link)
                seen_titles.add(key_title)

                content = entry.get("content") or []
                raw_summary = (
                    entry.get("summary")
                    or entry.get("description")
                    or (content[0].get("value", "") if content else "")
                )
                articles.append({
                    "category": category,
                    "title": title,
                    "source": feed_config["name"],
                    "link": link,
                    "published": published.isoformat(),
                    "fetched_at": fetched_at,
                    "feed_url": url,
                    "summary": clean_text(raw_summary)[:1200],
                })
            print(
                f"Read [{category}] {feed_config['name']}: "
                f"{len(feed.entries)} entries, {recent_count} recent, "
                f"{missing_date_count} without date"
            )
    selected = select_articles(articles)
    for article in selected:
        article["id"] = material_id(article)
    return selected, feed_issues
