import { memo, useMemo } from "react";
import { MultiFileDiff } from "@pierre/diffs/react";

import type { DiffData, ToolExecutionChunk } from "@/features/agents/lib/types";
import { useDiffOptions } from "@/features/agents/utils/diffUtils";

/**
 * A diff rendered as a work-entry body. Unlike the standalone diff card this
 * carries no header or toggle of its own — the row above it owns both.
 */
export const DiffEntryBody = memo(function DiffEntryBody({
  diffData,
  originalContent,
  newContent,
}: {
  diffData: DiffData;
  originalContent: string;
  newContent: string;
}) {
  const diffOptions = useDiffOptions();
  const inlineDiffOptions = useMemo(
    () => ({ ...diffOptions, disableFileHeader: true }),
    [diffOptions],
  );

  if (diffData.isBinary) {
    return <p className="font-mono text-[11px] text-muted-foreground">Binary file — no diff</p>;
  }

  return (
    <div className="max-h-64 overflow-auto rounded-md border border-border/60">
      <MultiFileDiff
        oldFile={{ name: diffData.filePath, contents: originalContent }}
        newFile={{ name: diffData.filePath, contents: newContent }}
        options={inlineDiffOptions}
      />
    </div>
  );
});

export const ShellEntryBody = memo(function ShellEntryBody({
  chunk,
}: {
  chunk: ToolExecutionChunk;
}) {
  const command = typeof chunk.input?.command === "string" ? chunk.input.command : "";
  const output = chunk.output ?? "";

  return (
    <div className="space-y-1.5">
      {command && (
        <pre className="cursor-text select-text overflow-x-auto whitespace-pre font-mono text-[11px] leading-relaxed text-foreground/85">
          <span className="text-muted-foreground/60">$ </span>
          {command}
        </pre>
      )}
      {output && (
        <pre className="max-h-64 cursor-text select-text overflow-auto whitespace-pre font-mono text-[11px] leading-relaxed text-muted-foreground">
          {output}
        </pre>
      )}
      {!output && chunk.status === "in_progress" && (
        <p className="font-mono text-[11px] text-muted-foreground">Running…</p>
      )}
      {!output && chunk.status === "pending" && (
        <p className="font-mono text-[11px] text-warning-foreground">Waiting for approval…</p>
      )}
    </div>
  );
});

export function DiffStatChip({
  additions,
  deletions,
}: {
  additions: number;
  deletions: number;
}) {
  if (additions === 0 && deletions === 0) return null;
  return (
    <span className="flex shrink-0 items-center gap-1 font-mono text-[10px] tabular-nums">
      {additions > 0 && <span className="text-success-foreground">+{additions}</span>}
      {deletions > 0 && <span className="text-destructive-foreground">-{deletions}</span>}
    </span>
  );
}
