import { memo } from "react";

import type { ToolExecutionChunk } from "@/features/agents/lib/types";

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
