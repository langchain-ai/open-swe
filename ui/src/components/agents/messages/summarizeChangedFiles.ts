import { countLineChanges } from "@/components/agents/utils/diffStats";
import type { Chunk, DiffData } from "@/lib/agents/types";

import type { ChangedFileSummaryItem } from "./types";

export function summarizeChangedFiles(chunks: Chunk[]): ChangedFileSummaryItem[] {
  const byFile = new Map<string, { filePath: string; originalContent: string | null; modifiedContent: string }>();

  for (const chunk of chunks) {
    if (chunk.kind !== "tool-execution") continue;
    if (chunk.status !== "completed") continue;
    const diffEntries = chunk.diffs?.length ? chunk.diffs : (chunk.diffData ? [chunk.diffData] : []);
    if (diffEntries.length === 0) continue;

    for (const diffData of diffEntries as DiffData[]) {
      const existing = byFile.get(diffData.filePath);

      if (!existing) {
        byFile.set(diffData.filePath, {
          filePath: diffData.filePath,
          originalContent: diffData.originalContent,
          modifiedContent: diffData.newContent,
        });
        continue;
      }

      byFile.set(diffData.filePath, {
        filePath: existing.filePath,
        originalContent: existing.originalContent,
        modifiedContent: diffData.newContent,
      });
    }
  }

  return [...byFile.values()]
    .map((file) => {
      const { additions, deletions } = countLineChanges(
        file.originalContent,
        file.modifiedContent,
        file.filePath,
      );
      return {
        filePath: file.filePath,
        additions,
        deletions,
        originalContent: file.originalContent ?? "",
        modifiedContent: file.modifiedContent,
      };
    })
    .sort((a, b) => a.filePath.localeCompare(b.filePath));
}
