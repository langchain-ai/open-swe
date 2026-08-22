import { memo } from "react"

import type { ToolExecutionChunk } from "@/lib/agentTypes"
import { ToolResultBody } from "./ToolResultBody"

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
        <pre className="cursor-text overflow-x-auto font-mono text-[12px] leading-relaxed whitespace-pre text-foreground/85 select-text">
          <span className="text-muted-foreground/80">$ </span>
          {command}
        </pre>
      )}
      {output && <ToolResultBody value={output} />}
      {!output && chunk.status === "in_progress" && (
        <p className="font-mono text-[12px] text-muted-foreground">Running…</p>
      )}
      {!output && chunk.status === "pending" && (
        <p className="font-mono text-[12px] text-warning-foreground">
          Waiting for approval…
        </p>
      )}
    </div>
  )
})
