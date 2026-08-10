import { memo } from "react"

import type { ToolExecutionChunk } from "@/features/agents/lib/types"

export const ShellEntryBody = memo(function ShellEntryBody({
  chunk,
}: {
  chunk: ToolExecutionChunk
}) {
  const command =
    typeof chunk.input?.command === "string" ? chunk.input.command : ""
  const output = chunk.output ?? ""

  return (
    <div className="space-y-1.5">
      {command && (
        <pre className="cursor-text overflow-x-auto font-mono text-[11px] leading-relaxed whitespace-pre text-foreground/85 select-text">
          <span className="text-muted-foreground/60">$ </span>
          {command}
        </pre>
      )}
      {output && (
        <pre className="max-h-64 cursor-text overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre text-muted-foreground select-text">
          {output}
        </pre>
      )}
      {!output && chunk.status === "in_progress" && (
        <p className="font-mono text-[11px] text-muted-foreground">Running…</p>
      )}
      {!output && chunk.status === "pending" && (
        <p className="font-mono text-[11px] text-warning-foreground">
          Waiting for approval…
        </p>
      )}
    </div>
  )
})
