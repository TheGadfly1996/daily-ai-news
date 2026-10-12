import io
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import generate


class Response:
    def __init__(self, payload, content_type="application/rss+xml"):
        self.payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.payload[:limit]


class FeedValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"category": "科技前沿", "name": "示例来源", "url": "https://example.com/feed"}

    def fetch(self, payload, content_type="application/rss+xml"):
        with patch.object(generate, "urlopen", return_value=Response(payload, content_type)):
            return generate.fetch_feed(self.config)

    def test_valid_and_empty_rss_are_available(self):
        valid = self.fetch(b'<rss version="2.0"><channel><title>News</title><item><title>Story</title></item></channel></rss>')
        empty = self.fetch(b'<rss version="2.0"><channel><title>News</title></channel></rss>')
        self.assertIsNone(valid.error)
        self.assertEqual(len(valid.entries), 1)
        self.assertIsNone(empty.error)
        self.assertEqual(empty.entries, [])

    def test_html_and_invalid_json_are_reported(self):
        html = self.fetch(b"<html><title>Cloudflare challenge</title></html>", "text/html")
        json_feed = self.fetch(b'{"items": "not a list"}', "application/json")
        self.assertIn("RSS 解析失败", html.error)
        self.assertIn("JSON 响应不是有效的 Feed", json_feed.error)

    def test_http_error_is_reported_without_stopping_other_feeds(self):
        configs = [self.config, {**self.config, "name": "故障来源", "url": "https://example.org/broken"}]
        recent = datetime.now(timezone.utc).isoformat()
        good = SimpleNamespace(error=None, entries=[{
            "title": "一条新闻", "link": "https://example.com/story", "summary": "摘要",
            "published_parsed": datetime.fromisoformat(recent).timetuple(),
        }])
        bad = SimpleNamespace(error="HTTP 404", entries=[])
        with patch.object(generate, "load_feeds", return_value=configs), patch.object(
            generate, "fetch_feed", side_effect=[good, bad]
        ):
            articles, issues = generate.collect_articles()
        self.assertEqual(len(articles), 1)
        self.assertEqual(issues, [{"category": "科技前沿", "name": "故障来源",
                                   "url": "https://example.org/broken", "reason": "HTTP 404"}])

        error = HTTPError(self.config["url"], 403, "Forbidden", {}, io.BytesIO())
        with patch.object(generate, "urlopen", side_effect=error):
            self.assertEqual(generate.fetch_feed(self.config).error, "HTTP 403")


if __name__ == "__main__":
    unittest.main()
