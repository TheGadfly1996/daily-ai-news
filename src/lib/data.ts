import { parseDigest, validateRecords, type Digest } from "./content.ts";
import { validateEvidence } from "./evidence.ts";

// One JSON file per issue: data/digests/YYYY-MM-DD.json
const files = import.meta.glob("../../data/digests/*.json", { eager: true, import: "default" });

let cache: Digest[] | undefined;
export function getDigests(): Digest[] {
  if (!cache) {
    const records = Object.entries(files).map(([path, record]) => {
      const date = path.match(/(\d{4}-\d{2}-\d{2})\.json$/)?.[1];
      if ((record as { date?: string }).date !== date) throw new Error("Digest file name differs from its date: " + path);
      return record;
    });
    cache = validateRecords(records).map(record => {
      validateEvidence(record);
      return parseDigest(record);
    });
  }
  return cache;
}
