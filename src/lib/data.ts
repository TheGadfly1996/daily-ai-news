import records from "../../data/digests.json";
import { parseDigest, validateRecords, type Digest } from "./content.ts";
import { validateEvidence } from "./evidence.ts";

let cache: Digest[] | undefined;
export function getDigests(): Digest[] {
  if (!cache) {
    cache = validateRecords(records).map(record => {
      validateEvidence(record);
      return parseDigest(record);
    });
  }
  return cache;
}
