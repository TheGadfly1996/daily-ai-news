import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, readdirSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { createHash } from "node:crypto";
import { parseFragment } from "parse5";
import { parseDigest, validateRecords, textContent, normalizeTitle, categories, type DigestRecord } from "../src/lib/content.ts";
import { renderFeed } from "../src/lib/rss.ts";
import { renderFeedIssues } from "../src/lib/feed-issues.ts";
import { validateEvidence } from "../src/lib/evidence.ts";
import { activeSectionId } from "../src/lib/section-spy.ts";

const record: DigestRecord = {
  date: "2026-10-12", title: "每日新闻总结 | 2026-10-12", guid: "daily-news-2026-10-12",
  created_at: "2026-10-12T00:17:00+00:00", article_count: 2,
  content: '<h1>每日新闻总结</h1><p>概览 <strong>重点</strong></p><h2>AI每日总结</h2>' +
    '<h3>模型 A &amp; B 发布</h3><p>公司称完成更新。</p><p>来源：<a href="https://example.com/?a=1&amp;b=2">官方</a>、<a href="https://example.org/">研究机构</a></p>' +
    '<h3>第二项研究</h3><p>研究摘要。</p><p><a href="https://example.net/">来源：大学</a></p>' +
    '<h2>自然科学</h2><p>过去24小时暂无可核实的新内容。</p>',
};

test("adapts legacy HTML into independently rendered stories and source links", () => {
  const digest = parseDigest(record);
  assert.equal(digest.storyCount, 2);
  assert.equal(digest.overviewText, "概览 重点");
  assert.equal(digest.sections[0].id, "ai");
  assert.equal(digest.sections[0].items[0].title, "模型 A & B 发布");
  assert.equal(digest.sections[0].items[0].sources[0].url, "https://example.com/?a=1&b=2");
  assert.equal(digest.sections[0].items[1].sources[0].name, "大学");
  assert.equal(digest.sections[1].items.length, 0);
  assert.match(digest.sections[1].emptyHtml, /暂无可核实/);
});

test("removes executable HTML, unsafe links, and inline styles before rendering", () => {
  const digest = parseDigest({ ...record, content: record.content +
    '<script>alert(1)</script><p onclick="bad()" style="color:red"><a href="javascript:alert(1)">不安全链接</a><img src=x onerror=bad()></p>' });
  assert.doesNotMatch(digest.content, /<script|onclick|onerror|javascript:|style=|<img/);
});

test("retains text-only attribution and deduplicates identical source links", () => {
  const content = '<h2>国内新闻</h2><h3>事件</h3><p>摘要</p><p>来源：新华社</p>' +
    '<p>来源：<a href="https://example.org/">机构</a>、<a href="https://example.org/">机构</a></p>';
  const item = parseDigest({ ...record, content }).sections[0].items[0];
  assert.deepEqual(item.sources, [{ name: "新华社" }, { name: "机构", url: "https://example.org/" }]);
});

test("rejects duplicate dates, invalid calendar dates and invalid timestamps", () => {
  assert.throws(() => validateRecords([record, record]), /Duplicate/);
  assert.throws(() => validateRecords([{ ...record, date: "2026-02-30" }]), /date/);
  assert.throws(() => validateRecords([{ ...record, created_at: "invalid" }]), /timestamp/);
  assert.throws(() => validateRecords([]), /empty/);
});

test("all historical stories survive the build-time content adaptation", () => {
  const records = validateRecords(JSON.parse("[" + readdirSync("data/digests").sort().map(name => readFileSync("data/digests/" + name, "utf8")).join(",") + "]"));
  for (const record of records) {
    const original = parseFragment(record.content).childNodes
      .filter(node => "tagName" in node && node.tagName === "h3").map(textContent);
    const digest = parseDigest(record);
    assert.deepEqual(digest.sections.flatMap(section => section.items.map(item => item.title)), original, record.date);
    assert.equal(digest.sections.length, 6, record.date);
  }
});

test("RSS preserves existing HTML URLs, escapes XML and splits CDATA delimiters", () => {
  const digest = parseDigest({ ...record, title: "A & B", content: record.content + "<p>]]></p>" });
  const feed = renderFeed([digest], "https://example.org/daily-ai-news");
  assert.match(feed, /A &amp; B/);
  assert.match(feed, /https:\/\/example.org\/daily-ai-news\/digests\/2026-10-12.html/);
  // Sanitization encodes angle brackets, preventing a CDATA boundary from becoming executable markup.
  assert.match(feed, /<description><!\[CDATA\[/);
  assert.match(feed, /Mon, 12 Oct 2026 00:17:00 GMT/);
});

test("failed feed details appear at the end of the article and RSS item safely", () => {
  const issue = { category: "科技前沿", name: "来源 <测试>",
    url: "https://example.org/rss?a=1&b=2", reason: "HTTP 403 <blocked>" };
  const digest = parseDigest({ ...record, feed_issues: [issue] });
  const notice = renderFeedIssues(digest.feed_issues);
  assert.match(notice, /本次未能读取的 RSS 来源/);
  assert.match(notice, /来源 &lt;测试&gt;/);
  assert.match(notice, /href="https:\/\/example.org\/rss\?a=1&amp;b=2"/);
  assert.doesNotMatch(notice, /<blocked>|<测试>/);
  const feed = renderFeed([digest], "https://example.org/daily-ai-news");
  assert.ok(feed.indexOf("本次未能读取的 RSS 来源") > feed.indexOf("自然科学"));
  assert.throws(() => validateRecords([{ ...record, feed_issues: [{ ...issue, url: "javascript:alert(1)" }] }]), /feed issues/);
});

const structured: DigestRecord = JSON.parse(readFileSync("tests/fixtures/digest-v2.json", "utf8"));

test("structured content renders identically to Python and ignores a stale HTML cache", () => {
  const parsed = parseDigest(validateRecords([structured])[0]);
  assert.equal(parsed.content, structured.content);
  assert.equal(parsed.storyCount, 18);
  assert.equal(parsed.sections[0].items[0].sources[0].name, "研究机构 <示例>");
  const changed = structuredClone(structured);
  changed.content = "<h1>stale content</h1>";
  assert.equal(parseDigest(changed).content, structured.content);
  assert.match(renderFeed([parsed], "https://example.org"), /研究机构 &lt;示例&gt;/);
});

test("structured records reject forged references, incomplete sections, unsafe URLs and invalid windows", () => {
  const mutations: ((record: DigestRecord) => void)[] = [
    record => { record.editorial!.sections[0].items[0].source_ids = []; },
    record => { record.editorial!.sections[0].items[0].source_ids = ["invented"]; },
    record => { record.editorial!.sections[0].items[0].source_ids = [record.materials![3].id]; },
    record => { record.editorial!.sections[0].items[0].source_ids = [record.materials![1].id]; },
    record => { record.editorial!.sections[0].items.pop(); },
    record => { record.materials![0].link = "javascript:alert(1)"; },
    record => { record.materials![0].published = record.generation!.window_end; },
    record => { record.materials![1].id = record.materials![0].id; },
    record => { record.generation!.window_start = record.generation!.window_end; },
    record => { record.generation!.minimum_items = 0; },
    record => { record.schema_version = 999; },
  ];
  for (const mutate of mutations) {
    const value = structuredClone(structured);
    mutate(value);
    assert.throws(() => validateRecords([value]));
  }
});

test("legacy attribution preserves text and links in the same paragraph", () => {
  const digest = parseDigest({ ...record, content: '<h2>国内新闻</h2><h3>事件</h3><p>摘要</p>' +
    '<p>来源：新华社、<a href="https://example.org/">机构</a>、研究团队</p>' });
  assert.deepEqual(digest.sections[0].items[0].sources, [
    { name: "新华社" }, { name: "机构", url: "https://example.org/" }, { name: "研究团队" },
  ]);
});

test("all archive dates remain available while RSS includes only recent 60 issues", () => {
  const records = Array.from({ length: 75 }, (_, index) => ({ ...record,
    date: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10), guid: "test-" + index,
  }));
  const digests = validateRecords(records).map(parseDigest);
  assert.equal(digests.length, 75);
  assert.equal(digests.at(-1)!.date, "2026-01-01");
  const feed = renderFeed(digests, "https://example.org/");
  assert.equal((feed.match(/<item>/g) || []).length, 60);
  assert.doesNotMatch(feed, /digests\/2026-01-01.html/);
});

test("build rejects absent or mismatched evidence for structured issues", () => {
  const root = mkdtempSync(join(tmpdir(), "digest-evidence-"));
  try {
    const value = structuredClone(structured);
    const prompt = "fixture prompt";
    value.generation!.prompt_sha256 = createHash("sha256").update(prompt).digest("hex");
    const path = join(root, value.generation!.evidence_path);
    assert.throws(() => validateEvidence(value, root));
    mkdirSync(dirname(path), { recursive: true });
    const evidence = { ...value.generation, issue_date: value.date, status: "validated", prompt,
      config: { min_items_per_category: 3 }, materials: value.materials,
      attempts: [{ status: "valid", finish_reason: "stop", raw_response: JSON.stringify(value.editorial) }],
    };
    writeFileSync(path, JSON.stringify(evidence));
    assert.doesNotThrow(() => validateEvidence(value, root));
    evidence.materials![0].summary += " changed";
    writeFileSync(path, JSON.stringify(evidence));
    // Break object aliasing before comparing against the original snapshot.
    value.materials = structuredClone(structured.materials);
    assert.throws(() => validateEvidence(value, root), /Evidence differs/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("section evidence accepts a truncated retry and rejects lost or modified stage outputs", () => {
  const root = mkdtempSync(join(tmpdir(), "section-evidence-"));
  try {
    const value = structuredClone(structured);
    value.generation!.strategy = "sections-v1";
    const system = "fixture system prompt";
    const requests = [...value.editorial!.sections, { overview: value.editorial!.overview }].map((result, index) => {
      const stage = index < 6 ? "section" : "overview";
      const category = index < 6 ? value.editorial!.sections[index].category : undefined;
      return { stage, category, result, attempts: [{ stage, category: category ?? null,
        prompt: "fixture prompt " + index, feedback: "", max_tokens: 6000, reasoning_effort: "low",
        status: "valid", finish_reason: "stop", raw_response: JSON.stringify(result) }],
      };
    });
    const failed = { ...requests[1].attempts[0], status: "invalid", finish_reason: "length", raw_response: "{" };
    requests[1].attempts.unshift(failed);
    requests[1].attempts[1].max_tokens = 12000;
    requests[1].attempts[1].feedback = "retry compactly";
    const prompt = JSON.stringify(requests.map(request => ({ stage: request.stage, category: request.category ?? null,
      system_prompt: system, attempts: request.attempts.map(attempt => ({ prompt: attempt.prompt, feedback: attempt.feedback,
        max_tokens: attempt.max_tokens, reasoning_effort: attempt.reasoning_effort })),
    })));
    value.generation!.prompt_sha256 = createHash("sha256").update(prompt).digest("hex");
    const evidence = { ...value.generation, issue_date: value.date, status: "validated", prompt,
      system_prompt: system, config: { min_items_per_category: 3 }, materials: value.materials,
      editorial: value.editorial, requests, attempts: requests.flatMap(request => request.attempts),
    };
    const path = join(root, value.generation!.evidence_path);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(evidence));
    assert.doesNotThrow(() => validateEvidence(value, root));
    const invalidCases = [
      (copy: typeof evidence) => { copy.requests.pop(); },
      (copy: typeof evidence) => { copy.requests[0].attempts[0].raw_response = "{}"; },
      (copy: typeof evidence) => { copy.requests[0].attempts[0].finish_reason = "length"; },
      (copy: typeof evidence) => { copy.requests[0].attempts[0].prompt += " tampered"; },
      (copy: typeof evidence) => { copy.attempts.pop(); },
      (copy: typeof evidence) => { copy.requests[6].category = "国内新闻"; },
    ];
    for (const mutate of invalidCases) {
      const copy = structuredClone(evidence);
      mutate(copy);
      writeFileSync(path, JSON.stringify(copy));
      assert.throws(() => validateEvidence(value, root));
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("title normalization matches the vectors shared with Python", () => {
  const cases: [string, string][] = JSON.parse(readFileSync("tests/fixtures/normalize-cases.json", "utf8"));
  for (const [raw, expected] of cases) assert.equal(normalizeTitle(raw), expected, raw);
});

test("categories come from the shared config in a fixed order", () => {
  const config = JSON.parse(readFileSync("config/news.json", "utf8"));
  assert.deepEqual(categories, config.categories);
  assert.equal(categories.length, 6);
});

test("archived issues generated under older, looser length limits stay valid", () => {
  const fixture = JSON.parse(readFileSync("tests/fixtures/digest-v2.json", "utf8"));
  fixture.editorial.overview = "概".repeat(168);
  fixture.editorial.sections[0].items[0].summary = "摘".repeat(300);
  assert.doesNotThrow(() => validateRecords([fixture]));
  fixture.editorial.overview = "概".repeat(2001);
  assert.throws(() => validateRecords([fixture]));
});

test("navigation highlights the section that crossed the reading line", () => {
  const tops = (...values: number[]) => values.map((top, index) => ({ id: "s" + index, top }));
  assert.equal(activeSectionId([], 800, false), undefined);
  assert.equal(activeSectionId(tops(500, 1200, 1900), 800, false), undefined);
  assert.equal(activeSectionId(tops(200, 1200, 1900), 800, false), "s0");
  assert.equal(activeSectionId(tops(-900, 100, 900), 800, false), "s1");
  assert.equal(activeSectionId(tops(-2000, -900, -50), 800, false), "s2");
  assert.equal(activeSectionId(tops(-900, -300, 600), 800, true), "s2");
});

test("site icons exist and are valid image files", () => {
  const svg = readFileSync("public/favicon.svg", "utf8");
  assert.match(svg, /^<svg[^>]+viewBox="0 0 64 64"/);
  assert.doesNotMatch(svg, /<script|onload|href=/i);
  const png = readFileSync("public/apple-touch-icon.png");
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(png.readUInt32BE(16), 180);
  assert.equal(png.readUInt32BE(20), 180);
  const ico = readFileSync("public/favicon.ico");
  assert.equal(ico.readUInt16LE(2), 1);                       // 图标类型
  assert.equal(ico.readUInt16LE(4), 1);                       // 一张图
  assert.equal(ico.readUInt32LE(14), ico.length - ico.readUInt32LE(18));  // 数据长度与偏移一致
  assert.deepEqual([...ico.subarray(22, 30)], [137, 80, 78, 71, 13, 10, 26, 10]);
});
