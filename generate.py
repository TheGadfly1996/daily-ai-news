import os
import re
import json
import html
import calendar
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

API_KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = os.getenv("OPENROUTER_MODEL") or "deepseek/deepseek-v4.1-flash"

repo = os.getenv("GITHUB_REPOSITORY", "username/daily-ai-news")
owner, repo_name = repo.split("/", 1)

SITE_URL = os.getenv(
    "SITE_URL",
    f"https://{owner}.github.io/{repo_name}"
).rstrip("/")


def load_feed_urls():
    urls = []

    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        urls.append(line)

    return urls


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

    return datetime.now(timezone.utc)


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"\s+", "", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]", "", title)

    return title


def collect_articles():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)

    articles = []
    seen_links = set()
    seen_titles = set()

    for url in load_feed_urls():

        print(f"Reading: {url}")

        feed = feedparser.parse(
            url,
            agent="Mozilla/5.0 Daily-AI-News-RSS/1.0"
        )

        source_name = feed.feed.get("title", url)

        for entry in feed.entries:

            published = entry_datetime(entry)

            if published < cutoff:
                continue

            title = clean_text(entry.get("title", ""))

            link = (
                entry.get("link")
                or entry.get("guid")
                or ""
            )

            if not title:
                continue

            normalized = normalize_title(title)

            # 基础去重
            if link and link in seen_links:
                continue

            if normalized in seen_titles:
                continue

            if link:
                seen_links.add(link)

            seen_titles.add(normalized)

            raw_summary = (
                entry.get("summary")
                or entry.get("description")
                or entry.get("content", [{}])[0].get("value", "")
                if entry.get("content")
                else ""
            )

            summary = clean_text(raw_summary)

            # 防止把全文全部发给模型
            summary = summary[:1200]

            articles.append({
                "title": title,
                "source": source_name,
                "link": link,
                "published": published.isoformat(),
                "summary": summary,
            })

    articles.sort(
        key=lambda x: x["published"],
        reverse=True,
    )

    return articles[:MAX_ARTICLES]


def build_prompt(articles):
    article_text = json.dumps(
        articles,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
以下是过去 {LOOKBACK_HOURS} 小时来自多个 RSS 新闻源的新闻。

你是一名专业、克制、中立的全球新闻编辑。

你的任务不是逐篇摘要，而是把这些文章整理成一份
“每日全球新闻早报”。

要求：

1. 识别多个媒体报道的同一新闻事件，将它们合并。
2. 不要因为某个事件报道数量多，就认为它一定更重要。
3. 从全部新闻中选出真正重要的 10～15 个事件。
4. 宁缺毋滥，不需要为了凑数量加入娱乐八卦或价值很低的消息。
5. 优先考虑：
   - 全球重大事件
   - 国际关系与地缘政治
   - 中国、美国、欧洲和主要经济体
   - 宏观经济和央行政策
   - 金融市场
   - 科技
   - 人工智能
   - 具有长期影响的重要社会事件
6. 对存在争议或不同说法的事件，不要擅自判断哪一方正确。
7. 不要补充输入资料里不存在的具体事实。
8. 每个事件都尽量保留原始新闻链接。
9. 使用简体中文。
10. 不要输出 Markdown。
11. 只输出 HTML fragment，不要输出 ```html。
12. 只允许使用：
    h1 h2 h3 p ul li strong em a blockquote

建议结构：

<h1>今日全球新闻早报</h1>

<p>150～250字概括今天整体新闻脉络。</p>

<h2>全球要闻</h2>

<h3>1. 标题</h3>
<p>发生了什么，以及为什么值得关注。</p>
<p>来源：<a href="原文URL">媒体名称</a></p>

<h2>财经与市场</h2>

...

<h2>科技与 AI</h2>

...

<h2>未来24～72小时值得关注</h2>

<ul>
<li>...</li>
</ul>

不得进行没有信息依据的预测。

RSS 新闻数据如下：

{article_text}
"""


def generate_digest(articles):

    if not articles:
        raise RuntimeError("过去24小时没有抓到任何文章")

    client = OpenAI(
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是一名中立、准确、重视信息密度的全球新闻编辑。",
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

<title>我的每日全球新闻早报</title>

<link>{SITE_URL}</link>

<description>
由多个RSS新闻源和AI自动生成的每日全球新闻摘要
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
<title>每日全球新闻早报</title>
</head>

<body>

<h1>每日全球新闻早报</h1>

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
        "title": f"每日全球新闻早报 | {today}",
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
