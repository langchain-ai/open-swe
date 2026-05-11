import { readFileSync } from "fs";
import { buildDiffLines } from "./diff.js";
import type { DiffLine, StructuredPatchHunk } from "@types";

const EMPTY_LINES: string[] = [];

function splitDiffContent(content: string): string[] {
  if (content.length === 0) return EMPTY_LINES;
  return content.split("\n");
}

function countLinesBefore(content: string, index: number): number {
  let count = 0;
  for (let i = 0; i < index; i++) {
    if (content.charCodeAt(i) === 10) count++;
  }
  return count;
}

function findLineStartInCurrentFile(
  filePath: string | undefined,
  needle: string,
): number {
  if (!filePath || needle.length === 0) return 1;
  try {
    const content = readFileSync(filePath, "utf8");
    const index = content.indexOf(needle);
    return index >= 0 ? countLinesBefore(content, index) + 1 : 1;
  } catch {
    return 1;
  }
}

export function diffLinesToStructuredHunk(
  diffLines: DiffLine[],
  lineStart?: number,
): StructuredPatchHunk[] {
  if (diffLines.length === 0) return [];

  let oldLines = 0;
  let newLines = 0;
  const lines = diffLines.map((line) => {
    if (line.type === "add") {
      newLines++;
      return `+${line.text}`;
    }
    if (line.type === "remove") {
      oldLines++;
      return `-${line.text}`;
    }
    oldLines++;
    newLines++;
    return ` ${line.text}`;
  });

  const hunkStart =
    lineStart ??
    diffLines.find((line) => line.oldLine !== undefined || line.newLine !== undefined)
      ?.oldLine ??
    diffLines.find((line) => line.newLine !== undefined)?.newLine ??
    1;

  return [
    {
      oldStart: hunkStart,
      oldLines,
      newStart: hunkStart,
      newLines,
      lines,
    },
  ];
}

export function createStructuredPatchHunks({
  oldContent,
  newContent,
  lineStart = 1,
}: {
  oldContent: string;
  newContent: string;
  lineStart?: number;
}): StructuredPatchHunk[] {
  if (oldContent === newContent) return [];
  const oldLines = splitDiffContent(oldContent);
  const newLines = splitDiffContent(newContent);
  return diffLinesToStructuredHunk(buildDiffLines(oldLines, newLines), lineStart);
}

export function buildToolPatch(toolName: string | undefined, args: Record<string, any>) {
  const name = toolName ?? "";
  const filePath = String(args.file_path ?? args.path ?? "");

  if (name === "edit_file" || name === "edit" || name === "apply_diff") {
    const oldString = args.old_string;
    const newString = args.new_string;
    if (typeof oldString !== "string" || typeof newString !== "string") {
      return null;
    }
    const lineStart = findLineStartInCurrentFile(filePath, newString);
    return {
      filePath,
      hunks: createStructuredPatchHunks({
        oldContent: oldString,
        newContent: newString,
        lineStart,
      }),
    };
  }

  if (name === "write_file" || name === "write") {
    const content = args.content;
    if (typeof content !== "string") return null;
    return {
      filePath,
      hunks: createStructuredPatchHunks({
        oldContent: "",
        newContent: content,
        lineStart: 1,
      }),
    };
  }

  return null;
}

export function parseStructuredDiffOutput(output: string): StructuredPatchHunk[] | null {
  try {
    const parsed = JSON.parse(output);
    if (Array.isArray(parsed?.hunks)) return parsed.hunks as StructuredPatchHunk[];
    if (Array.isArray(parsed?.diffLines)) {
      return diffLinesToStructuredHunk(parsed.diffLines as DiffLine[]);
    }
  } catch {
    return null;
  }
  return null;
}
