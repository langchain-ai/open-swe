import { parseDiffFromFile } from "@pierre/diffs";

export function countLineChanges(
  originalContent: string | null | undefined,
  newContent: string,
  filePath = "file",
): { additions: number; deletions: number } {
  const meta = parseDiffFromFile(
    { name: filePath, contents: originalContent ?? "" },
    { name: filePath, contents: newContent },
  );

  let additions = 0;
  let deletions = 0;
  for (const hunk of meta.hunks) {
    additions += hunk.additionLines;
    deletions += hunk.deletionLines;
  }
  return { additions, deletions };
}
