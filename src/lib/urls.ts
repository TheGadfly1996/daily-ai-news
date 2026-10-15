// 站点部署路径（如 /daily-ai-news/），由 astro.config.mjs 的 base 决定
const base = import.meta.env.BASE_URL.replace(/\/?$/, "/");
/** 拼接带部署路径的链接。 */
export function urlFor(path = ""): string {
  const relativePath = path.replace(/^\/+/, "");
  return relativePath ? base + relativePath : base === "/" ? "/" : base.slice(0, -1);
}
/** 日期页链接：开发时无扩展名，发布时输出 .html，兼容既有书签与 RSS 链接。 */
export function digestUrl(date: string): string {
  return urlFor("digests/" + date + (import.meta.env.DEV ? "" : ".html"));
}
