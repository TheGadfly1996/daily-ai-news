# 新闻网站主题

页面右上方的“主题”选择器提供 9 个 StyleKit 主题。默认使用极简扁平风，
选项保存到浏览器的 localStorage；首页、归档详情和同源标签页会沿用选择。
存储不可用时仍可切换当前页面。栏目总标题保留不同颜色，新闻标题使用正文色。

| 主题 | 视觉规则 |
| --- | --- |
| minimalist-flat / 极简扁平 | 黑白、直角、2px 边框、零阴影、即时反色 |
| corporate-clean / 专业企业 | 蓝色强调、浅色背景、细边框、8px 圆角、轻阴影 |
| neo-brutalist / 新野兽派 | 粗黑边框、直角、硬阴影、等宽正文、亮色强调 |
| natural-organic / 自然有机 | 米色与大地色、有机圆角、衬线标题、叶片装饰 |
| hand-drawn-doodle / 手绘涂鸦 | 奶白纸张、虚线边框、标记笔硬阴影、笔记横线 |
| memphis / 孟菲斯 | 黄粉青撞色、几何装饰、粗边框、硬阴影 |
| comic-style / 漫画 | 墨线粗边框、硬阴影、网点、概览气泡 |
| cottagecore / 田园乡村 | 温暖花卉色、衬线排版、柔和圆角、轻阴影、叶片装饰 |
| pixel-art / 复古像素 | 8-bit 调色板、等宽字体、直角粗边框、硬阴影、即时状态切换 |

规则来自本地的九份 StyleKit 文件（原路径为
`~/.codex/skills/style/*-SKILL.md`）。文件内部存在冲突时优先采用禁止项，
例如漫画卡片保持直角，仅概览气泡使用圆角；像素主题采用即时交互。
为保证新闻阅读和对比度，暖色主题使用更深的正文和栏目标题颜色。
装饰只位于概览与首页区域。

## 修改和构建

- src/styles/layout.css：响应式阅读布局。
- src/styles/themes.css：Tailwind 入口、主题 token 和专属组件样式。
- src/components/ThemePicker.vue：Vue 主题切换、保存与跨标签页同步。
- src/lib/themes.ts：主题名称和选项的统一定义。
- src/layouts/BaseLayout.astro：首次加载时恢复偏好，避免主题闪烁。

安装、检查和构建：

~~~sh
npm ci
npm run check
npm test
npm run build
~~~

开发预览：

~~~sh
npm run dev
~~~

Astro 通过 Tailwind 的 Vite 插件构建主题样式，并输出到 dist/_astro/。
所有 HTML、CSS 与 Vue 浏览器代码都在本地构建，不使用 Tailwind CDN。
构建直接读取已保存的新闻 JSON，不会请求 AI。
GitHub Actions 部署 dist/。完整开发与发布流程见项目 README。
