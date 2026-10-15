import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import type { DigestRecord } from "./content.ts";

type Attempt = {
  stage: string; category: string | null; prompt: string; feedback: string; max_tokens: number;
  reasoning_effort: string | null; status: string; finish_reason: string; raw_response: string;
  source_aliases?: Record<string, string>;
};
type Request = { stage: string; category?: string; attempts: Attempt[]; result: unknown };
function responseValue(attempt: Attempt): unknown {
  if (attempt?.status !== "valid" || attempt.finish_reason !== "stop" || typeof attempt.raw_response !== "string") {
    throw new Error("Response did not finish successfully");
  }
  const value = JSON.parse(attempt.raw_response.trim().replace(/^```(?:json)?\s*|\s*```$/gi, ""));
  // 模型引用的是每次请求内的短别名，比较前先映射回真实素材 ID
  const aliases = attempt.source_aliases;
  if (aliases && Array.isArray(value?.items)) {
    for (const item of value.items) {
      if (Array.isArray(item?.source_ids)) item.source_ids = item.source_ids.map((ref: string) => aliases[ref] ?? ref);
    }
  }
  return value;
}

/**
 * 核对新记录与证据文件一致，不一致则让构建失败：证据存在、运行 ID/模型/窗口/素材相同、
 * 提示词哈希匹配，并且每个栏目与概览的有效响应能还原出当期内容。旧版记录无证据，直接跳过。
 */
export function validateEvidence(record: DigestRecord, root = process.cwd()): void {
  if (record.schema_version !== 2) return;
  const generation = record.generation!;
  if (!new RegExp(`^data/runs/${record.date}/[a-zA-Z0-9-]+\\.json$`).test(generation.evidence_path)) {
    throw new Error("Invalid evidence path: " + record.date);
  }
  const evidence = JSON.parse(readFileSync(resolve(root, generation.evidence_path), "utf8"));
  if (evidence.status !== "validated" || evidence.issue_date !== record.date ||
      evidence.run_id !== generation.run_id || evidence.model !== generation.model ||
      evidence.prompt_version !== generation.prompt_version || typeof evidence.prompt !== "string" ||
      evidence.window_start !== generation.window_start || evidence.window_end !== generation.window_end ||
      evidence.config?.min_items_per_category !== generation.minimum_items ||
      !isDeepStrictEqual(evidence.materials, record.materials) ||
      createHash("sha256").update(evidence.prompt).digest("hex") !== generation.prompt_sha256 ||
      evidence.prompt_sha256 !== generation.prompt_sha256) {
    throw new Error("Evidence differs from digest: " + record.date);
  }
  if (generation.strategy === "sections-v1") {
    if (evidence.strategy !== generation.strategy || !Array.isArray(evidence.requests) || evidence.requests.length !== 7 ||
        typeof evidence.system_prompt !== "string" || !isDeepStrictEqual(evidence.editorial, record.editorial)) {
      throw new Error("Invalid section generation evidence: " + record.date);
    }
    const requests = evidence.requests as Request[];
    requests.forEach((request, index) => {
      const section = record.editorial!.sections[index];
      if (!Array.isArray(request.attempts) || !request.attempts.length ||
          request.stage !== (index < 6 ? "section" : "overview") ||
          request.category !== section?.category) throw new Error("Invalid request stage");
      const expected = index < 6 ? section : { overview: record.editorial!.overview };
      if (!isDeepStrictEqual(responseValue(request.attempts.at(-1)!), expected) ||
          !isDeepStrictEqual(request.result, expected)) throw new Error("Section response differs from digest");
      for (const attempt of request.attempts) {
        if (attempt.stage !== request.stage || attempt.category !== (request.category ?? null) ||
            typeof attempt.prompt !== "string" || typeof attempt.feedback !== "string" ||
            !Number.isInteger(attempt.max_tokens) || attempt.max_tokens <= 0) throw new Error("Invalid attempt evidence");
      }
    });
    const manifest = requests.map(request => ({ stage: request.stage, category: request.category ?? null,
      system_prompt: evidence.system_prompt,
      attempts: request.attempts.map(attempt => ({ prompt: attempt.prompt, feedback: attempt.feedback,
        max_tokens: attempt.max_tokens, reasoning_effort: attempt.reasoning_effort })),
    }));
    if (!isDeepStrictEqual(JSON.parse(evidence.prompt), manifest) ||
        !isDeepStrictEqual(evidence.attempts, requests.flatMap(request => request.attempts))) {
      throw new Error("Request manifest differs from evidence");
    }
    return;
  }
  if (evidence.strategy !== undefined || !isDeepStrictEqual(responseValue(evidence.attempts?.at(-1)), record.editorial)) {
    throw new Error("Validated response differs from digest: " + record.date);
  }
}
