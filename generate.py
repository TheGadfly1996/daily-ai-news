import os
import re
import json
import html
import calendar
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.request import Request, urlopen
from datetime import datetime, timezone, timedelta
from pathlib import Path

import bleach
import feedparser
from openai import OpenAI


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
DIGEST_DIR = PUBLIC_DIR / "digests"

HISTORY_FILE = DATA_DIR / "digests.json"
FEEDS_FILE = ROOT / "feeds.txt"

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "80"))
FEED_TIMEOUT = int(os.getenv("FEED_TIMEOUT", "15"))

API_KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = os.getenv("OPENROUTER_MODEL") or "deepseek/deepseek-v4.1-flash"

repo = os.getenv("GITHUB_REPOSITORY", "username/daily-ai-news")
owner, repo_name = repo.split("/", 1)

SITE_URL = os.getenv(
    "SITE_URL",
    f"https://{owner}.github.io/{repo_name}"
).rstrip("/")


CATEGORIES = (
    "国际重大新闻", "国内新闻", "AI每日总结", "贸易财经", "科技前沿", "自然科学",
)


def load_feeds():
    feeds = []
    for line_number, line in enumerate(FEEDS_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3 or parts[0] not in CATEGORIES or not parts[1] or not parts[2].startswith(("https://", "http://")):
            raise ValueError(f"feeds.txt 第 {line_number} 行格式错误")
        category, name, url = parts
        feeds.append({"category": category, "name": name, "url": url})
    return feeds


def clean_text(value):
    if not value:
        return ""

    value = re.sub(r"<[^>]+>", " ", str(value))
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def entry_datetime(entry):
    for key in ("published_parsed", "updated_parsed"):

        value = entry.get(key)

        if value:
            timestamp = calendar.timegm(value)
            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

    return None


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"\s+", "", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]", "", title)

    return title


def fetch_feed(feed_config):
    url = feed_config["url"]
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 Daily-AI-News-RSS/1.0"})
        with urlopen(request, timeout=FEED_TIMEOUT) as response:
            payload = response.read(5_000_000)
            content_type = response.headers.get("Content-Type", "")
        if "json" in content_type or url.endswith(".json"):
            data = json.loads(payload)
            entries = []
            for item in data.get("items", []):
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
            return SimpleNamespace(entries=entries, bozo=False)
        return feedparser.parse(payload)
    except Exception as exc:
        print(f"Feed failed: {url}: {exc}")
        return SimpleNamespace(entries=[], bozo=False)


def select_articles(articles):
    """Reserve space for each category before filling remaining slots by recency."""
    if MAX_ARTICLES <= 0:
        return []
    ordered = sorted(articles, key=lambda item: item["published"], reverse=True)
    selected = []
    selected_ids = set()
    quota = MAX_ARTICLES // len(CATEGORIES)
    for category in CATEGORIES:
        matches = [item for item in ordered if item["category"] == category]
        for item in matches[:quota]:
            selected.append(item)
            selected_ids.add(id(item))
    for item in ordered:
        if len(selected) >= MAX_ARTICLES:
            break
        if id(item) not in selected_ids:
            selected.append(item)
    return selected


def collect_articles():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    articles = []
    seen_links = set()
    seen_titles = set()

    feed_configs = load_feeds()
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = pool.map(fetch_feed, feed_configs)
        for feed_config, feed in zip(feed_configs, fetched):
            url = feed_config["url"]
            category = feed_config["category"]
            print(f"Read [{category}]: {url} ({len(feed.entries)} entries)")
            if feed.bozo and not feed.entries:
                print(f"Feed failed: {url}: {feed.bozo_exception}")
                continue

            for entry in feed.entries:
                published = entry_datetime(entry)
                if published is None or published < cutoff:
                    continue
                title = clean_text(entry.get("title", ""))
                link = entry.get("link") or entry.get("guid") or ""
                if not title:
                    continue
                normalized = normalize_title(title)
                key_link = (category, link)
                key_title = (category, normalized)
                if (link and key_link in seen_links) or key_title in seen_titles:
                    continue
                if link:
                    seen_links.add(key_link)
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
                    "summary": clean_text(raw_summary)[:1200],
                })
    return select_articles(articles)


def build_prompt(articles):
    grouped = {category: [] for category in CATEGORIES}
    for article in articles:
        grouped[article["category"]].append({
            key: value for key, value in article.items() if key != "category"
        })
    article_text = json.dumps(grouped, ensure_ascii=False, indent=2)
    return f"""
以下是过去 {LOOKBACK_HOURS} 小时的 RSS 新闻，已按栏目分组。
你是一名准确、克制、中立的中文新闻编辑。请生成“每日新闻总结”。

要求：
1. 仅依据对应栏目下的文章写该栏目，不跨栏目挪用；同一事件的多篇报道合并。
2. 严格按以下顺序输出六个 h2 栏目：{', '.join(CATEGORIES)}。
3. 每栏选择真正重要的事件，优先概括事实、背景及影响；内容稀少时少写，空栏写“过去{LOOKBACK_HOURS}小时暂无可核实的新内容”。
4. AI 栏重点总结模型、产品、研究和行业动态；贸易财经栏重点总结贸易、宏观经济和市场动态。
5. 不把报道数量当作重要性，不添加输入中没有的事实，不做无依据预测。涉及争议时写清不同说法。
6. 每个事件用 h3 标题和 p 摘要，并在另一个 p 中用原文链接标明来源。没有链接时仅写来源名称。
7. 使用简体中文。只输出 HTML fragment，不输出 Markdown 或代码块。
8. 只使用 h1、h2、h3、p、ul、li、strong、em、a、blockquote 标签。

输出结构：<h1>每日新闻总结</h1><p>今日概览</p>，然后依次输出六个栏目。

RSS 新闻数据：
{article_text}
"""


def generate_digest(articles):

    if not articles:
        raise RuntimeError(f"过去{LOOKBACK_HOURS}小时没有抓到任何文章")

    client = OpenAI(
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是一名中立、准确、重视信息密度的中文新闻编辑。",
            },
            {"role": "user", "content": build_prompt(articles)},
        ],
    )

    result = (response.choices[0].message.content or "").strip()
    if not result:
        raise RuntimeError("OpenRouter 未返回新闻摘要内容")

    # 防止模型偶尔加入代码块
    result = re.sub(
        r"^```(?:html)?\s*|\s*```$",
        "",
        result,
        flags=re.I,
    )

    allowed_tags = [
        "h1", "h2", "h3",
        "p",
        "ul", "li",
        "strong", "em",
        "a",
        "blockquote",
    ]

    allowed_attributes = {
        "a": ["href"],
    }

    result = bleach.clean(
        result,
        tags=allowed_tags,
        attributes=allowed_attributes,
        protocols=["http", "https"],
        strip=True,
    )

    return result


def load_history():

    if not HISTORY_FILE.exists():
        return []

    try:
        return json.loads(
            HISTORY_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return []


def save_history(history):

    DATA_DIR.mkdir(exist_ok=True)

    HISTORY_FILE.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def safe_cdata(value):
    return value.replace("]]>", "]]]]><![CDATA[>")


def build_rss(history):

    items = []

    for item in history:

        item_url = (
            f"{SITE_URL}/digests/{item['date']}.html"
        )

        title = html.escape(item["title"])
        guid = html.escape(item["guid"])

        pub_date = datetime.fromisoformat(
            item["created_at"]
        ).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        )

        description = safe_cdata(
            item["content"]
        )

        items.append(f"""
<item>
<title>{title}</title>
<link>{item_url}</link>
<guid isPermaLink="false">{guid}</guid>
<pubDate>{pub_date}</pubDate>
<description><![CDATA[
{description}
]]></description>
</item>
""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>

<title>我的每日新闻总结</title>

<link>{SITE_URL}</link>

<description>
由六类RSS新闻源和AI自动生成的每日新闻总结
</description>

<language>zh-CN</language>

<lastBuildDate>
{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}
</lastBuildDate>

{''.join(items)}

</channel>
</rss>
"""

    (PUBLIC_DIR / "feed.xml").write_text(
        rss,
        encoding="utf-8",
    )


def build_html_pages(history):

    DIGEST_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    links = []

    for item in history:

        date = item["date"]
        title = html.escape(item["title"])

        page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>{title}</title>

<style>
body {{
    max-width: 820px;
    margin: 40px auto;
    padding: 0 20px;
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        "PingFang SC",
        sans-serif;
    line-height: 1.75;
}}

a {{
    word-break: break-all;
}}
</style>

</head>

<body>

{item["content"]}

</body>
</html>
"""

        (
            DIGEST_DIR /
            f"{date}.html"
        ).write_text(
            page,
            encoding="utf-8",
        )

        links.append(
            f'<li><a href="digests/{date}.html">'
            f'{title}</a></li>'
        )

    index = f"""<!doctype html>

<html lang="zh-CN">

<head>
<meta charset="utf-8">
<title>每日新闻总结</title>
</head>

<body>

<h1>每日新闻总结</h1>

<p>
<a href="feed.xml">RSS订阅地址</a>
</p>

<ul>
{''.join(links)}
</ul>

</body>
</html>
"""

    (PUBLIC_DIR / "index.html").write_text(
        index,
        encoding="utf-8",
    )


def main():

    PUBLIC_DIR.mkdir(exist_ok=True)

    print("Collecting RSS...")
    articles = collect_articles()

    print(
        f"Collected {len(articles)} articles"
    )

    print("Calling AI...")
    digest = generate_digest(articles)

    now = datetime.now(timezone.utc)

    # 北京日期
    china_time = now + timedelta(hours=8)
    today = china_time.strftime("%Y-%m-%d")

    new_item = {
        "date": today,
        "title": f"每日新闻总结 | {today}",
        "guid": f"daily-news-{today}",
        "created_at": now.isoformat(),
        "content": digest,
        "article_count": len(articles),
    }

    history = load_history()

    # 同一天重新运行时替换，而不是创建重复文章
    history = [
        item
        for item in history
        if item.get("date") != today
    ]

    history.insert(0, new_item)

    # 保留最近60期
    history = history[:60]

    save_history(history)
    build_rss(history)
    build_html_pages(history)

    print("Done")
    print(
        f"RSS: {SITE_URL}/feed.xml"
    )


if __name__ == "__main__":
    main()
