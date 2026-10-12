import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import type { DigestRecord } from "./content.ts";

/** Fail the build if a new issue loses its input snapshot or points at a different run. */
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
  const last = evidence.attempts?.at(-1);
  if (last?.status !== "valid" || last.finish_reason !== "stop" || typeof last.raw_response !== "string" ||
      !isDeepStrictEqual(JSON.parse(last.raw_response.trim().replace(/^```(?:json)?\s*|\s*```$/gi, "")), record.editorial)) {
    throw new Error("Validated response differs from digest: " + record.date);
  }
}
