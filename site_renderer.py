"""Render the static news site without network or AI dependencies."""

from __future__ import annotations

import html
import re
from pathlib import Path


SECTIONS = (
    ("国际重大新闻", "world"),
    ("国内新闻", "china"),
    ("AI每日总结", "ai"),
    ("贸易财经", "business"),
    ("科技前沿", "technology"),
    ("自然科学", "science"),
)


def _page(title: str, stylesheet: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#ffffff">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{stylesheet}">
  <link rel="alternate" type="application/rss+xml" title="每日新闻总结 RSS" href="{('../' if stylesheet.startswith('../') else '')}feed.xml">
</head>
<body id="top">
{body}
</body>
</html>
"""


def _header(prefix: str) -> str:
    return f"""<header class="site-header">
  <div class="site-header-inner">
    <a class="brand" href="{prefix}index.html" aria-label="每日新闻总结首页">每日新闻<span class="brand-mark">.</span></a>
    <nav class="site-nav" aria-label="主导航">
      <a href="{prefix}index.html">归档</a>
      <a href="{prefix}feed.xml">RSS 订阅</a>
    </nav>
  </div>
</header>"""


def _footer(prefix: str) -> str:
    return f"""<footer class="site-footer">
  <div class="site-footer-inner">
    <span>每日新闻总结</span>
    <a href="{prefix}index.html">返回归档 ↑</a>
  </div>
</footer>"""


def _digest_content(content: str) -> str:
    for heading, anchor in SECTIONS:
        original = f"<h2>{heading}</h2>"
        content = content.replace(
            original,
            f'<h2 id="{anchor}">{heading}</h2>',
            1,
        )
    parts = re.split(r'(<h2 id="(?:world|china|ai|business|technology|science)">)', content)
    result = [parts[0]]
    for index in range(1, len(parts), 2):
        anchor = re.search(r'id="([^"]+)"', parts[index]).group(1)
        result.append(f'<section class="news-section category-{anchor}">{parts[index]}{parts[index + 1]}</section>')
    return "".join(result)


def _digest_page(item: dict) -> str:
    date = html.escape(item["date"])
    content = _digest_content(item["content"])
    intro = re.match(r"\s*<h1>(.*?)</h1>\s*<p>(.*?)</p>", content, re.S)
    introduction = ""
    if intro:
        introduction = f"""<h1>{intro.group(1)}</h1>
    <div class="digest-overview">
      <p class="overview-label">今日概览</p>
      <p>{intro.group(2)}</p>
    </div>"""
        content = content[intro.end():]
    links = "\n".join(
        f'<a class="category-{anchor}" href="#{anchor}">{html.escape(heading)}</a>'
        for heading, anchor in SECTIONS
    )
    body = f"""{_header('../')}
<main id="main-content" class="page digest-page">
  <header class="digest-intro">
  <div class="issue-meta">
    <span>每日简报</span>
    <time datetime="{date}">{date}</time>
  </div>
  {introduction}
  </header>
  <div class="digest-layout">
  <nav class="section-nav" aria-label="跳转到栏目">
    <span class="sidebar-label">栏目导航</span>
{links}
  </nav>
  <article class="digest-body">
{content}
  </article>
  </div>
</main>
<a class="back-to-top" href="#top" aria-label="回到页面顶部">回到顶部 ↑</a>
{_footer('../')}"""
    return _page(item["title"], "../style.css", body)


def _index_page(history: list[dict]) -> str:
    latest = history[0]
    date = html.escape(latest["date"])
    latest_title = html.escape(latest["title"])
    archive_items = history[1:]
    archive = "\n".join(
        f"""<li><a class="archive-card" href="digests/{html.escape(item['date'])}.html">
          <time datetime="{html.escape(item['date'])}">{html.escape(item['date'])}</time>
          <span>{html.escape(item['title'])}</span>
          <span class="card-arrow" aria-hidden="true">↗</span>
        </a></li>"""
        for item in archive_items
    )
    body = f"""{_header('')}
<main id="main-content" class="page">
  <section class="index-hero" aria-labelledby="site-title">
    <p class="eyebrow">NEWS / DAILY / RSS</p>
    <h1 id="site-title">每日新闻总结<span class="accent-dot">.</span></h1>
    <p class="hero-copy">国际、国内、AI、贸易财经、科技前沿与自然科学。每天一页，保留来源，方便追溯。</p>
  </section>
  <section class="latest-section" aria-labelledby="latest-heading">
    <div class="section-heading">
      <h2 id="latest-heading">最新一期</h2>
      <span>01 / TODAY</span>
    </div>
    <a class="latest-card" href="digests/{date}.html">
      <span class="card-label">READ THE LATEST</span>
      <strong>{latest_title}</strong>
      <span class="latest-card-bottom"><time datetime="{date}">{date}</time><span aria-hidden="true">↗</span></span>
    </a>
  </section>
  <section class="archive-section" aria-labelledby="archive-heading">
    <div class="section-heading">
      <h2 id="archive-heading">往期归档</h2>
      <span>{len(archive_items):02d} ISSUES</span>
    </div>
    <ol class="archive-list">
{archive}
    </ol>
  </section>
</main>
{_footer('')}"""
    return _page("每日新闻总结 | 归档", "style.css", body)


def build_html_pages(history: list[dict], public_dir: Path) -> None:
    if not history:
        raise ValueError("没有可生成的新闻归档")
    digest_dir = public_dir / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    for item in history:
        (digest_dir / f"{item['date']}.html").write_text(
            _digest_page(item), encoding="utf-8"
        )
    (public_dir / "index.html").write_text(
        _index_page(history), encoding="utf-8"
    )
