# 配置说明（config/news.json）

JSON 不支持注释，各项含义集中写在这里。本地与 GitHub Actions 使用同一份配置；
`lookback_hours`、`max_articles`、`max_per_source`、`min_items_per_category`、`feed_timeout` 可用同名大写环境变量覆盖，
模型可用 `OPENROUTER_MODEL` 覆盖。启动时会校验配置（`newsgen/settings.py` 的 `validate_config`）。

## 采集

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `lookback_hours` | `24` | 采集窗口长度（小时）。窗口为 [期号当天截止时刻 − 该值, 截止时刻)。 |
| `issue_cutoff` | `"06:00"` | 每期窗口的截止时刻（北京时间 HH:MM）。修改时需同步修改 daily.yml 的定时时间。 |
| `max_articles` | `100` | 送给模型的素材总数上限；均分到六栏，每栏配额约为该值 ÷ 6。 |
| `max_per_source` | `10` | 同一栏目内单个来源最多入选的素材数，避免被某一来源占满。 |
| `feed_timeout` | `20` | 抓取单个 RSS 来源的超时（秒）。 |
| `min_items_per_category` | `3` | 每栏最少事件数；任一栏素材少于该值时不调用模型、当期失败。环境变量 `MIN_ITEMS_PER_CATEGORY` 可覆盖。 |
| `max_items_per_category` | `10` | 每栏最多事件数（提示词与校验都会强制）；若小于最少条数，则取最少条数。 |

## 模型调用

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `model` | `"deepseek/deepseek-v4.1-flash"` | 默认模型（OpenRouter 的模型 ID）；环境变量 `OPENROUTER_MODEL` 优先。 |
| `temperature` | `0.3` | 采样温度（0–2），新闻编辑建议较低；`null` 表示不发送。 |
| `response_format` | `"json_object"` | `json_object` 要求模型输出 JSON；`null` 表示不发送（模型不支持时使用）。程序仍会校验输出。 |
| `reasoning_effort` | `"low"` | 通过 OpenRouter 请求的推理强度：`minimal`/`low`/`medium`/`high`，`null` 表示不发送。低强度可避免推理占满输出预算；是否生效取决于模型与供应商。 |
| `api_timeout` | `90` | 单次模型请求的超时（秒）。 |
| `generation_attempts` | `2` | 每个阶段（单个栏目或概览）的最多尝试次数，不是整期总请求数。 |
| `max_output_tokens` | `16000` | 单次请求输出 token 的硬上限；截断重试时预算翻倍也不会超过它。 |
| `section_output_tokens` | `6000` | 每个栏目请求的初始输出预算。 |
| `overview_output_tokens` | `1500` | 概览请求的初始输出预算。 |

## 补齐缺失期号

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `backfill_days` | `3` | 补齐缺失期号时向前检查的天数；`0` 表示不补齐。RSS 只保留较新的条目，过早的期号无法重建。 |
| `backfill_start_deadline_seconds` | `900` | 开始运行后超过该秒数就不再启动新的补齐，避免总耗时超过工作流超时。 |

## 栏目与长度（Python 与 Astro 共用）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `categories` | `六个栏目` | 栏目列表（id 与标题），顺序即页面与校验顺序。Python 与 Astro 共用；增减栏目还需同步 feeds.txt、样式中的栏目颜色与提示词。 |
| `title_max_chars` | `200` | 事件标题最大字符数（新生成与读取归档都用）。 |
| `summary_max_chars` | `160` | 新生成时摘要与概览的硬上限；提示词建议 120。 |
| `archive_text_max_chars` | `2000` | 读取已归档数据时摘要与概览的宽松上限，避免旧提示词生成的内容被拒绝。 |

## 发布

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `rss_limit` | `60` | RSS 订阅输出最近多少期；网页始终包含全部历史。 |

## 条数怎么控制

- 每栏条数范围：`min_items_per_category`～`max_items_per_category`，提示词与 `digest_schema.validate_section` 都会强制。
- 提示词要求素材足够时尽量写满上限；素材不足时只写真实事件，不凑数。
- 输出被截断后重试时，条数上限减半（不低于最少条数），摘要缩到 100 字符。
- 候选素材决定实际上限：每栏约 `max_articles ÷ 6` 条候选，且同一素材只能用于一个事件。
