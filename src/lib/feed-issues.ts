import type { FeedIssue } from "./content.ts";

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function renderFeedIssues(issues: FeedIssue[] | undefined): string {
  if (!issues?.length) return "";
  const items = issues.map(issue =>
    `<li><strong>${escapeHtml(issue.category)} · ${escapeHtml(issue.name)}</strong>` +
    `<a href="${escapeHtml(issue.url)}">${escapeHtml(issue.url)}</a>` +
    `<span>${escapeHtml(issue.reason)}</span></li>`
  ).join("");
  return `<aside class="feed-issues" aria-labelledby="feed-issues-heading">` +
    `<h2 id="feed-issues-heading">本次未能读取的 RSS 来源</h2>` +
    `<p>以下来源在生成本期内容时不可用，可能是链接失效或暂时无法连接。请检查 feeds.txt。</p>` +
    `<ul>${items}</ul></aside>`;
}
