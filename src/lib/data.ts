import { parseDigest, validateRecords, type Digest } from "./content.ts";
import { validateEvidence } from "./evidence.ts";

// 每期一个 JSON：data/digests/YYYY-MM-DD.json，构建时一次性读入
const files = import.meta.glob("../../data/digests/*.json", { eager: true, import: "default" });

// 解析结果缓存：各页面共用，避免重复校验
let cache: Digest[] | undefined;
/** 返回全部期号（新到旧）；文件名与日期不一致、记录或证据校验失败都会让构建失败。 */
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
