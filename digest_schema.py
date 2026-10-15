"""Versioned editorial data; source names and links are never supplied by the model."""
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

# Categories and length limits live in config/news.json so Python and the Astro build share one definition.
_SHARED = json.loads((Path(__file__).parent / "config/news.json").read_text(encoding="utf-8"))
CATEGORIES = tuple(item["title"] for item in _SHARED["categories"])
TITLE_MAX = _SHARED["title_max_chars"]
SUMMARY_MAX = _SHARED["summary_max_chars"]
PROMPT_VERSION = "2026-10-15.sections-v1.1"
SCHEMA_VERSION = 2


def safe_url(value):
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f]", value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def material_id(article):
    identity = "\0".join(article.get(key, "") for key in ("category", "source", "link", "title", "published"))
    return "src-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def normalized_title(value):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.lower())


def text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} 必须是非空文本，最多 {limit} 字符")
    return value.strip()


def validate_section(section, category, articles, minimum, titles=None, used_sources=None,
                     maximum=None, summary_limit=SUMMARY_MAX):
    sources = {}
    for article in articles:
        source_id = article.get("id")
        if not source_id or source_id in sources:
            raise ValueError("素材 ID 缺失或重复")
        if article.get("link") and not safe_url(article["link"]):
            raise ValueError("素材链接无效")
        sources[source_id] = article
    titles = titles if titles is not None else set()
    used_sources = used_sources if used_sources is not None else set()
    if not isinstance(section, dict) or set(section) != {"category", "items"} or section["category"] != category:
        raise ValueError("栏目不完整或顺序错误")
    items = section["items"]
    if not isinstance(items, list) or len(items) < minimum:
        raise ValueError(f"栏目条目不足：{category}，需要至少 {minimum} 个独立事件；不得凑数")
    if maximum is not None and len(items) > maximum:
        raise ValueError(f"栏目事件最多 {maximum} 条")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"title", "summary", "source_ids"}:
            raise ValueError("事件必须仅包含 title、summary、source_ids")
        title = normalized_title(text(item["title"], "title", TITLE_MAX))
        text(item["summary"], "summary", summary_limit)
        if not title or title in titles:
            raise ValueError("事件标题为空或重复")
        titles.add(title)
        refs = item["source_ids"]
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("每条事件必须引用素材 ID")
        if len(refs) != len(set(refs)):
            raise ValueError("事件中存在重复素材 ID")
        for ref in refs:
            if ref not in sources or sources[ref]["category"] != category:
                raise ValueError("素材 ID 不存在或属于其他栏目")
            if ref in used_sources:
                raise ValueError("同一素材不能拆成多条事件")
            used_sources.add(ref)
    return section


def validate_editorial(value, articles, minimum):
    if not isinstance(value, dict) or set(value) != {"overview", "sections"}:
        raise ValueError("输出必须仅包含 overview 和 sections")
    text(value["overview"], "overview", SUMMARY_MAX)
    sections = value["sections"]
    if not isinstance(sections, list) or len(sections) != len(CATEGORIES):
        raise ValueError("必须包含六个栏目")
    titles, used_sources = set(), set()
    for category, section in zip(CATEGORIES, sections):
        validate_section(section, category, articles, minimum, titles, used_sources)
    return value


def render_editorial(value, articles):
    sources = {article["id"]: article for article in articles}
    escape = html.escape
    parts = ["<h1>每日新闻总结</h1>", f"<p>{escape(value['overview'])}</p>"]
    for section in value["sections"]:
        parts.append(f"<h2>{escape(section['category'])}</h2>")
        for item in section["items"]:
            parts.extend([f"<h3>{escape(item['title'])}</h3>", f"<p>{escape(item['summary'])}</p>"])
            links = []
            for ref in item["source_ids"]:
                source = sources[ref]
                name, url = escape(source["source"]), source.get("link", "")
                if url and not safe_url(url):
                    raise ValueError("素材链接无效")
                link = f'<a href="{escape(url, quote=True)}">{name}</a>' if url else name
                if link not in links:
                    links.append(link)
            parts.append("<p>来源：" + "、".join(links) + "</p>")
    return "\n".join(parts)
