import { parseFragment, serialize, serializeOuter, type DefaultTreeAdapterMap } from "parse5";
import sanitizeHtml from "sanitize-html";

type Node = DefaultTreeAdapterMap["node"];
export const categories = [
  { id: "world", title: "国际重大新闻" },
  { id: "china", title: "国内新闻" },
  { id: "ai", title: "AI每日总结" },
  { id: "business", title: "贸易财经" },
  { id: "technology", title: "科技前沿" },
  { id: "science", title: "自然科学" },
] as const;

export interface Source { name: string; url?: string }
export interface NewsItem { id: string; title: string; bodyHtml: string; sources: Source[] }
export interface NewsSection { id: string; title: string; items: NewsItem[]; emptyHtml: string }
export interface FeedIssue { category: string; name: string; url: string; reason: string }
export interface Material {
  id: string; category: string; title: string; summary: string; source: string; link: string;
  published: string; fetched_at: string; feed_url: string;
}
export interface Editorial {
  overview: string;
  sections: { category: string; items: { title: string; summary: string; source_ids: string[] }[] }[];
}
export interface Generation {
  run_id: string; model: string; prompt_version: string; prompt_sha256: string;
  window_start: string; window_end: string; minimum_items: number; evidence_path: string;
}
export interface DigestRecord {
  date: string; title: string; guid: string; created_at: string; content: string; article_count: number;
  feed_issues?: FeedIssue[];
  schema_version?: number; editorial?: Editorial; materials?: Material[]; generation?: Generation;
}
export interface Digest extends DigestRecord {
  heading: string; overviewHtml: string; overviewText: string; sections: NewsSection[]; storyCount: number;
}
const sanitization = {
  allowedTags: ["h1", "h2", "h3", "p", "ul", "li", "strong", "em", "a", "blockquote", "br"],
  allowedAttributes: { a: ["href"] },
  allowedSchemes: ["https", "http"],
  allowProtocolRelative: false,
};

export function textContent(node: Node): string {
  if (node.nodeName === "#text") return (node as DefaultTreeAdapterMap["textNode"]).value;
  return "childNodes" in node ? node.childNodes.map(textContent).join("") : "";
}
function sourceParts(node: Node): (Source | string)[] {
  if ("tagName" in node && node.tagName === "a") return [{
    name: textContent(node).replace(/^来源[：:]\s*/, "").trim(),
    url: node.attrs.find(attribute => attribute.name === "href")?.value,
  }];
  if (node.nodeName === "#text") return [textContent(node)];
  return "childNodes" in node ? node.childNodes.flatMap(sourceParts) : [];
}

function escape(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid structured object");
  return value as Record<string, unknown>;
}
function requiredText(value: unknown, field: string, max = 2000): asserts value is string {
  if (typeof value !== "string" || !value.trim() || value.length > max) throw new Error("Invalid structured field: " + field);
}
function safeUrl(value: unknown): boolean {
  if (typeof value !== "string" || /[\s\x00-\x1f]/.test(value)) return false;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !!url.hostname && !url.username && !url.password;
  } catch { return false; }
}
function timestamp(value: unknown): number {
  requiredText(value, "timestamp");
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("Invalid timestamp");
  return Date.parse(value);
}

export function validateStructured(record: DigestRecord): void {
  const editorial = object(record.editorial);
  const generation = object(record.generation);
  for (const key of ["run_id", "model", "prompt_version", "prompt_sha256", "evidence_path"]) requiredText(generation[key], key);
  if (!Number.isInteger(generation.minimum_items) || (generation.minimum_items as number) < 1) throw new Error("Invalid minimum_items");
  const start = timestamp(generation.window_start), end = timestamp(generation.window_end);
  if (start >= end) throw new Error("Invalid collection window");
  if (new Date(end + 8 * 3600_000).toISOString().slice(0, 10) !== record.date) throw new Error("Issue date differs from window");
  requiredText(editorial.overview, "overview");
  if (!Array.isArray(record.materials) || record.materials.length !== record.article_count) throw new Error("Invalid materials");
  const sources = new Map<string, Material>();
  for (const value of record.materials) {
    const source = object(value);
    for (const key of ["id", "category", "title", "source"]) requiredText(source[key], key);
    if (!categories.some(category => category.title === source.category) || sources.has(source.id as string)) throw new Error("Invalid or duplicate material ID");
    if (typeof source.summary !== "string" || typeof source.link !== "string" || (source.link && !safeUrl(source.link)) || !safeUrl(source.feed_url)) throw new Error("Invalid material source");
    const published = timestamp(source.published);
    timestamp(source.fetched_at);
    if (published < start || published >= end) throw new Error("Material outside collection window");
    sources.set(source.id as string, value);
  }
  if (!Array.isArray(editorial.sections) || editorial.sections.length !== categories.length) throw new Error("Invalid structured sections");
  const titles = new Set<string>(), used = new Set<string>();
  editorial.sections.forEach((value, index) => {
    const section = object(value);
    if (section.category !== categories[index].title || !Array.isArray(section.items) || section.items.length < (generation.minimum_items as number)) throw new Error("Invalid section or insufficient items");
    for (const value of section.items) {
      const item = object(value);
      requiredText(item.title, "title", 200);
      requiredText(item.summary, "summary");
      const title = item.title.toLowerCase().replace(/[^\p{L}\p{N}_]/gu, "");
      if (!title || titles.has(title)) throw new Error("Duplicate or empty event title");
      titles.add(title);
      if (!Array.isArray(item.source_ids) || !item.source_ids.length) throw new Error("Missing source IDs");
      for (const ref of item.source_ids) {
        if (typeof ref !== "string" || sources.get(ref)?.category !== section.category || used.has(ref)) throw new Error("Invalid, cross-category or reused source ID");
        used.add(ref);
      }
    }
  });
}

function parseStructured(record: DigestRecord): Digest {
  validateStructured(record);
  const editorial = record.editorial!;
  const materials = new Map(record.materials!.map(source => [source.id, source]));
  const sections: NewsSection[] = editorial.sections.map((section, index) => ({
    id: categories[index].id, title: section.category, emptyHtml: "",
    items: section.items.map((item, itemIndex) => {
      const sources: Source[] = [];
      for (const ref of item.source_ids) {
        const material = materials.get(ref)!;
        const source = { name: material.source, url: material.link || undefined };
        if (!sources.some(existing => existing.name === source.name && existing.url === source.url)) sources.push(source);
      }
      return { id: categories[index].id + "-" + (itemIndex + 1), title: item.title,
        bodyHtml: "<p>" + escape(item.summary) + "</p>", sources };
    }),
  }));
  const overviewHtml = escape(editorial.overview);
  const content = ["<h1>每日新闻总结</h1>", "<p>" + overviewHtml + "</p>", ...sections.flatMap(section => [
    "<h2>" + escape(section.title) + "</h2>", ...section.items.flatMap(item => [
      "<h3>" + escape(item.title) + "</h3>", item.bodyHtml,
      "<p>来源：" + item.sources.map(source => source.url ? `<a href="${escape(source.url)}">${escape(source.name)}</a>` : escape(source.name)).join("、") + "</p>",
    ]),
  ])].join("\n");
  return { ...record, content, heading: "每日新闻总结", overviewHtml, overviewText: editorial.overview,
    sections, storyCount: sections.reduce((count, section) => count + section.items.length, 0) };
}

/** Adapt and sanitize legacy HTML at build time, without fetching archives in the browser. */
export function parseDigest(record: DigestRecord): Digest {
  if (record.schema_version === 2) return parseStructured(record);
  const content = sanitizeHtml(record.content, sanitization);
  const nodes = parseFragment(content).childNodes;
  let heading = "每日新闻总结";
  let overviewHtml = "";
  let overviewText = "";
  const sections: NewsSection[] = [];
  let section: NewsSection | undefined;
  let item: NewsItem | undefined;
  for (const node of nodes) {
    if (!("tagName" in node)) continue;
    const text = textContent(node).trim();
    if (node.tagName === "h1") {
      heading = text || heading;
    } else if (node.tagName === "h2") {
      const category = categories.find(category => category.title === text);
      const id = category?.id || "section-" + (sections.length + 1);
      if (sections.some(section => section.id === id)) throw new Error(record.date + ": duplicate section " + text);
      section = { id, title: text, items: [], emptyHtml: "" };
      sections.push(section);
      item = undefined;
    } else if (node.tagName === "h3") {
      if (!section) throw new Error(record.date + ": story appears before its section");
      item = { id: section.id + "-" + (section.items.length + 1), title: text, bodyHtml: "", sources: [] };
      section.items.push(item);
    } else if (node.tagName === "p" && /^来源[：:]/.test(text)) {
      if (!item) {
        if (section) section.emptyHtml += serializeOuter(node);
        continue;
      }
      const sources: Source[] = sourceParts(node).flatMap(part => typeof part === "string"
        ? part.replace(/^\s*来源[：:]\s*/, "").split(/[、，,；;]/).map(name => ({ name: name.trim() })).filter(source => source.name)
        : [part]);
      for (const source of sources) {
        if (source.name && !item.sources.some(existing => existing.name === source.name && existing.url === source.url)) {
          item.sources.push(source);
        }
      }
    } else if (!section && node.tagName === "p") {
      overviewHtml += serialize(node);
      overviewText += (overviewText ? " " : "") + text;
    } else if (item) {
      item.bodyHtml += serializeOuter(node);
    } else if (section) {
      section.emptyHtml += serializeOuter(node);
    }
  }
  return { ...record, content, heading, overviewHtml, overviewText, sections,
    storyCount: sections.reduce((count, section) => count + section.items.length, 0) };
}

export function validateRecords(input: unknown): DigestRecord[] {
  if (!Array.isArray(input) || input.length === 0) throw new Error("News history is empty or is not an array");
  const dates = new Set<string>();
  return input.map((value: unknown) => {
    if (typeof value !== "object" || value === null) throw new Error("Invalid digest record");
    const record = value as Record<string, unknown>;
    for (const key of ["date", "title", "guid", "created_at", "content"]) {
      if (typeof record[key] !== "string" || !record[key]) throw new Error("Invalid digest field: " + key);
    }
    const date = record.date as string;
    const parsedDate = new Date(date + "T00:00:00Z");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== date) {
      throw new Error("Invalid digest date: " + date);
    }
    if (!Number.isFinite(Date.parse(record.created_at as string))) throw new Error("Invalid timestamp: " + date);
    if (typeof record.article_count !== "number" || !Number.isInteger(record.article_count) || record.article_count < 0) {
      throw new Error("Invalid article count: " + date);
    }
    if (record.feed_issues !== undefined && (!Array.isArray(record.feed_issues) || record.feed_issues.some(issue =>
      typeof issue !== "object" || issue === null ||
      ["category", "name", "url", "reason"].some(key => typeof issue[key] !== "string" || !issue[key]) ||
      !/^https?:\/\//i.test(issue.url)
    ))) throw new Error("Invalid feed issues: " + date);
    if (dates.has(date)) throw new Error("Duplicate digest date: " + date);
    dates.add(date);
    if (record.schema_version !== undefined && record.schema_version !== 2) throw new Error("Unsupported schema version");
    if (record.schema_version === 2) validateStructured(record as unknown as DigestRecord);
    return record as unknown as DigestRecord;
  }).sort((a, b) => b.date.localeCompare(a.date));
}
