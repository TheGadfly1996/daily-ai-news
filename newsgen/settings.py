"""Paths, tunables and config validation. Other modules read these as `settings.X` at call time."""
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from digest_schema import CATEGORIES

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT / "feeds.txt"

CONFIG = json.loads((ROOT / "config/news.json").read_text(encoding="utf-8"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", CONFIG["lookback_hours"]))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", CONFIG["max_articles"]))
MAX_PER_SOURCE = int(os.getenv("MAX_PER_SOURCE", CONFIG["max_per_source"]))
MIN_ITEMS_PER_CATEGORY = int(os.getenv("MIN_ITEMS_PER_CATEGORY", CONFIG["min_items_per_category"]))
FEED_TIMEOUT = int(os.getenv("FEED_TIMEOUT", CONFIG["feed_timeout"]))
MODEL = os.getenv("OPENROUTER_MODEL") or CONFIG["model"]
CHINA = ZoneInfo("Asia/Shanghai")


def validate_config():
    if any(value <= 0 for value in (LOOKBACK_HOURS, MAX_ARTICLES, MAX_PER_SOURCE, MIN_ITEMS_PER_CATEGORY, FEED_TIMEOUT)):
        raise ValueError("采集配置必须为正整数")
    if MAX_ARTICLES < len(CATEGORIES) * MIN_ITEMS_PER_CATEGORY:
        raise ValueError("MAX_ARTICLES 不足以满足六栏最低条数")
    for key in ("api_timeout", "max_output_tokens", "section_output_tokens", "overview_output_tokens",
                "generation_attempts", "max_items_per_category"):
        if not isinstance(CONFIG[key], int) or CONFIG[key] <= 0:
            raise ValueError(f"{key} 必须为正整数")
    if CONFIG.get("reasoning_effort") not in (None, "low", "minimal", "medium", "high"):
        raise ValueError("reasoning_effort 必须为 low、minimal、medium、high 或 null")
    if CONFIG.get("response_format") not in (None, "json_object"):
        raise ValueError("response_format 必须为 json_object 或 null")
    temperature = CONFIG.get("temperature")
    if temperature is not None and not (isinstance(temperature, (int, float)) and 0 <= temperature <= 2):
        raise ValueError("temperature 必须在 0 到 2 之间或为 null")
    if not isinstance(CONFIG.get("model"), str) or not CONFIG["model"]:
        raise ValueError("model 必须为非空字符串")
