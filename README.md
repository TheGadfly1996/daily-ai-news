# 每日新闻总结

Python 采集 RSS 并生成新闻归档，Astro 构建静态新闻页面与 RSS，Vue 提供九主题切换，
Tailwind CSS 通过 Vite 编译主题样式。浏览器直接读取完整新闻 HTML；新闻正文不依赖 Vue。

## 本地开发

需要 Node.js 22.12 或更高版本。

~~~sh
npm ci
npm run dev
~~~

默认访问 http://localhost:4321/daily-ai-news。开发时日期页面使用无扩展名路由；
发布时仍输出 digests/YYYY-MM-DD.html，兼容既有书签与 RSS 链接。

检查、测试和构建：

~~~sh
npm run check
npm test
npm run build
npm run preview
~~~

构建结果在 dist/，默认预览地址为 http://localhost:4321/daily-ai-news。
这些命令使用现有 data/digests/，不调用 AI，也不需要 API Key。

## 更新新闻

~~~sh
python3 -m pip install -r requirements.txt
python3 generate.py
bash scripts/validate-site.sh
~~~

Python 需要通过环境变量提供 OPENROUTER_API_KEY，可通过 OPENROUTER_MODEL 选择模型。
它写入 data/digests/YYYY-MM-DD.json（只改当期文件），并将成功生成的证据记录写入 data/runs/。Astro 从同一份结构化数据构建网页与 feed.xml。
不要将 API Key 放进前端变量或提交到仓库。

只检查 feeds.txt 中的来源而不生成新闻，可运行 `python3 generate.py --check-feeds`；
命令会列出本次无法读取的来源与原因，全部可用时退出码为 0，否则为 1。
每日采集也会先检查来源；若有来源不可用，当期文章和 RSS 条目末尾会列出栏目、名称、链接与原因。
暂时连不上与永久失效都按“本次未能读取”记录，确认后再修改 feeds.txt。
网络错误、超时、HTTP 429/5xx 会重试一次；403/404 与内容无效不重试。
同一链接只会出现在先列出的栏目（按 feeds.txt 顺序），避免跨栏重复。

默认配置统一放在 `config/news.json`（每一项的含义见 [docs/config.md](docs/config.md)），本地与 Actions 使用同一份配置。
LOOKBACK_HOURS、MAX_ARTICLES、MAX_PER_SOURCE、MIN_ITEMS_PER_CATEGORY 和 FEED_TIMEOUT
仍可通过环境变量覆盖。默认每栏至少 3 个独立事件；任一栏素材不足时不调用 AI，
每个栏目独立校验，最多尝试 2 次；仍失败则保留原归档并停止发布，不使用占位条目凑数。

每期使用固定北京时间窗口：例如 2026-10-12 期覆盖前一天 08:17（含）至当天 08:17（不含）。
默认选择最近已经结束的窗口，因此任务延迟不会移动同一期的采集边界。
在当天 08:17 前手动运行会选择前一期。指定日期补跑：

~~~sh
python3 generate.py --date 2026-10-12
~~~

某期已存在时 `generate.py` 直接跳过（所以每天 08:17 与 09:47 两次定时是安全的，后者只在前者失败时补跑）；
需要覆盖已有期号时加 `--force`。不接受尚未结束的窗口。补跑仍从当前 RSS 抓取，较早内容可能已被上游移除；
固定窗口不能恢复上游已经删除的素材。修改截止时间时，应同步更新 daily.yml 的定时时间。

## 自动补齐缺失期号

每次不指定日期运行时，`generate.py` 先生成最新一期（失败则整体失败，原有保护不变），随后检查最近
`backfill_days`（默认 3）天内是否有缺失的期号，并从旧到新逐期补齐。补齐只会发生在第一期存档之后，
单期失败只发出警告、不影响最新一期；最新一期用掉太多时间时（超过 `backfill_start_deadline_seconds`，默认 900 秒）
不再启动新的补齐。补齐的期号在记录与证据里带 `backfilled` 标记。

补齐只能依据当前 RSS 重建历史窗口，较早内容可能已被上游移除，素材不足的期号会补齐失败并在日志中警告。
超过 `backfill_days` 的缺口不再自动尝试，可用 `--date` 手动补跑。
只查看缺口：`python3 generate.py --check-gaps`（有缺失时退出码为 1）；不想补齐：`--no-backfill`；
指定 `--date` 时只生成该期，不扫描缺口。每天 09:47 的第二次定时同样会触发补齐检查。

## 内容结构与证据

新生成记录使用 `schema_version: 2`，保存概览、六栏事件、实际输入素材及素材 ID。
模型仅返回标题、摘要与 `source_ids`，程序从素材回填来源名称和链接，转义所有文本。
不存在的 ID、跨栏引用、重复引用、重复标题、缺少来源和条数不足均会阻止写入。
HTML 是程序生成的兼容副本，前端以结构化字段为准；旧版 HTML 归档继续可读。
来源 ID 校验能保证引用存在，不能自动证明摘要的每句话都被来源支持；事实审核仍可依据快照进行。

生成按六个栏目逐一进行，默认每栏 3–10 个事件（素材足够时提示模型尽量写满上限），摘要最多 160 字符，然后仅根据已通过校验的事件生成概览。
正常情况共 7 次请求；重试只针对失败的栏目或概览，已接受的栏目不会在同一次运行中重新生成。
遇到 `finish_reason: length` 时丢弃截断响应，将该请求输出预算翻倍，并将栏目条数上限减半（不低于最低条数）、
摘要最多 100 字符；概览截断时缩短到最多 100 字符。预算不会超过配置上限，连续失败仍停止发布。
`generation_attempts` 表示每个部分的尝试上限，并非整期请求次数。

`section_output_tokens` 默认 6000，`overview_output_tokens` 默认 1500，
`max_output_tokens` 为单次请求最高上限（16000）。`model`、`temperature`（默认 0.3）和
`response_format`（默认 `json_object`，设为 `null` 则不发送）也在 `config/news.json` 中；`OPENROUTER_MODEL` 仍可覆盖模型。
发给模型的素材只含 id、来源、标题、北京时间发布时间和最多 600 字摘要；id 是每次请求内的短别名（A1、A2…），
程序在校验前映射回真实素材 ID，别名表随每次尝试写入证据。稳定的编辑规则放在提示词最前面，便于供应商复用前缀缓存。
摘要提示建议 120 字符以内，校验硬上限仍为 160。`max_items_per_category` 默认 10，
如果环境变量提高了最低条数，则至少满足该最低值。`reasoning_effort` 默认 `low`，通过 OpenRouter
统一参数请求较低推理强度，避免过多推理挤占正文预算；可设为 `null` 不发送该参数。
具体推理控制取决于模型和供应商支持情况。

`data/runs/YYYY-MM-DD/<run_id>.json` 保存实际素材摘要、采集窗口、系统与用户提示词、
提示词版本及哈希、请求模型、响应模型、原始输出、token 用量、耗时和重试反馈。
分栏生成的证据包含每个请求及重试的提示词、参数和结果；提示词哈希覆盖整个请求清单。
构建时检查证据文件是否存在、输入与提示词哈希是否匹配，以及每个栏目的有效响应和概览能否组成新闻对象。
旧版整期生成的证据仍兼容读取。截断的原始响应和 token 使用量也会保留，方便判断正文或推理预算耗尽。
保存的是实际送入模型的摘要（最多 1200 字符），不代表已抓取完整原文。

生成过程的检查点位于 `.runs/`，不提交到 Git；失败时保留原因和已取得的材料。
Actions 无论成功或失败都会上传诊断与候选数据，默认保留 14 天。成功证据随新闻数据提交。
历史归档永久保留，同日期重跑只替换该期；补跑旧期不会改变最新期排序。
网页生成全部历史日期页，RSS 仅输出最近 60 期（由 `rss_limit` 配置）。

## 提示词评测

改提示词或换模型前，可用已保存的 v2 素材回放并对比重试、截断与 token 数（需要 API Key，不写入 data/）：

~~~sh
python3 -m newsgen.evaluate 2026-10-14
python3 -m newsgen.evaluate --materials sample.json
~~~

旧版记录没有保存素材，无法回放。指标不判断事实准确性，仍需对照来源抽查标题。

分类与长度上限（`categories`、`title_max_chars`、`summary_max_chars`）在 `config/news.json` 中，
Python 与 Astro 共用；新生成按这些上限校验，读取已归档数据时用较宽的 `archive_text_max_chars`，
避免旧提示词生成的内容（如概览超过 160 字符）被拒绝；标题归一化用 `tests/fixtures/normalize-cases.json` 同时验证两端一致。

## 目录

~~~text
generate.py                 命令行入口：采集窗口、调度各阶段、保存归档
newsgen/                    settings 配置、feeds 采集、prompts 提示词、llm 调用与重试、storage 归档、evaluate 回放评测
digest_schema.py            结构化事件校验、素材 ID、确定性 HTML 渲染
config/news.json            本地与自动化共用默认配置
data/digests/              永久新闻归档，每期一个 YYYY-MM-DD.json，兼容历史 HTML 与新结构化记录
data/runs/                  成功生成的输入、提示词、响应与诊断快照
src/lib/content.ts          结构化内容渲染与历史 HTML 适配
src/lib/evidence.ts         构建时检查新闻与证据快照的一致性
src/lib/rss.ts              从同一份新闻数据生成 RSS
src/layouts/                Astro 文档布局、SEO、主题首次加载
src/components/             Astro 新闻组件、Vue 主题选择器
src/pages/                  首页、日期页面、RSS 和 404
src/styles/                 阅读布局与 Tailwind 九主题
public/                     原样复制的静态资源
tests/                      历史内容兼容、HTML 清洗和 RSS 校验
scripts/                    构建验证、提交与部署版本检查
dist/                       构建产物，不提交到 Git
~~~

## 发布

- Daily News Summary：继续按原计划每天北京时间 08:17 执行，
  生成候选数据，先完成类型检查、测试和 Astro 构建，再提交并推送数据，最后部署 dist/。
  手动触发可填写 `issue_date` 补跑指定期号。
- CI：非 main 分支推送与 Pull Request 运行 Python 测试和 `scripts/validate-site.sh`。
- 每日任务失败、Pages 部署失败时会自动创建 Issue（含运行链接）。
- daily.yml 拆成两个 job：build 持有 API Key 并可推送数据，但无 Pages 权限；deploy 只有 Pages/OIDC 权限。
  定时补跑发现该期已存在时不产生新提交，也不会重复部署。手动触发可勾选 `force` 覆盖已有期号。
- Build News Website：main 有推送时构建并发布，
  也可手动触发；不运行新闻采集或 AI。
- 两个工作流共用部署并发组，并在开始执行时检出最新 main，避免排队后构建旧事件版本。
- 如果 main 在生成或构建期间改变，任务停止，不自动将已验证结果 rebase 到未经验证的新代码上。
  发布前再次核对 main；已被更新替代的产物不会继续部署。产物包含 revision.txt，记录对应提交。
  停止后可在最新 main 重跑；若要复用已生成的内容，先从诊断 artifact 取回候选期和其证据目录，
  按日期合并到最新归档，运行 `bash scripts/validate-site.sh`，再提交；不要用旧 JSON 覆盖新归档。
- GitHub Pages 的 Source 应设置为 GitHub Actions。

本地完整验证（不访问 AI 或真实 RSS；发布测试只使用临时本地 Git 仓库）：

~~~sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash scripts/validate-site.sh
~~~

默认域名和部署路径取自 GITHUB_REPOSITORY，本地默认使用
https://TheGadfly1996.github.io/daily-ai-news/。
自定义域名可通过 SITE_URL 配置；BASE_PATH 可覆盖路径，例如根目录 /。
Astro 的资源链接、日期链接、canonical 和 RSS 都使用统一部署路径。

## 主题

默认极简扁平。选择器支持九种 StyleKit 风格，偏好保存到浏览器，
跨页面与标签页同步；浏览器禁用存储时仍可切换当前页面。
栏目颜色仅用于栏目总标题。侧边导航和回顶按钮保留；滚动时导航会高亮当前所在栏目（`aria-current="location"`，沿用各主题的悬停配色）。
详细规则和修改位置见 [docs/themes.md](docs/themes.md)。
