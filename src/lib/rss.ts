import type { Digest } from "./content.ts";
import { renderFeedIssues } from "./feed-issues.ts";
import config from "../../config/news.json" with { type: "json" };
function xml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&apos;");
}
// CDATA 中不能出现 "]]>"，需要拆开
function cdata(value: string): string {
  return value.replace(/]]>/g, "]]]]><![CDATA[>");
}
/** 生成 RSS：只输出最近 rss_limit 期，正文取自同一份结构化数据，末尾附带无法读取的来源。 */
export function renderFeed(digests: Digest[], siteUrl: string): string {
  const base = siteUrl.replace(/\/?$/, "/");
  const items = digests.slice(0, config.rss_limit).map(digest => [
    "<item><title>" + xml(digest.title) + "</title>",
    "<link>" + xml(base + "digests/" + digest.date + ".html") + "</link>",
    '<guid isPermaLink="false">' + xml(digest.guid) + "</guid>",
    "<pubDate>" + new Date(digest.created_at).toUTCString() + "</pubDate>",
    "<description><![CDATA[" + cdata(digest.content + renderFeedIssues(digest.feed_issues)) + "]]></description></item>",
  ].join("\n")).join("\n");
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0"><channel>',
    "<title>我的每日新闻总结</title><link>" + xml(base) + "</link>",
    "<description>由六类 RSS 新闻源和 AI 自动生成的每日新闻总结</description>",
    "<language>zh-CN</language>",
    "<lastBuildDate>" + new Date(digests[0].created_at).toUTCString() + "</lastBuildDate>",
    items, "</channel></rss>",
  ].join("\n");
}
