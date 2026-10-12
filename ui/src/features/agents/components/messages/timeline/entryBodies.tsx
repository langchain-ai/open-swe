import { memo } from "react"

import { ToolResultBody } from "./ToolResultBody"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

export const ShellEntryBody = memo(function ShellEntryBody({
  chunk,
  loadedText,
  loadError,
}: {
  chunk: ToolExecutionChunk
  /** Full output fetched on expand; the chunk alone holds only a preview. */
  loadedText?: string | null
  loadError?: string | null
}) {
  const command =
    typeof chunk.input?.command === "string" ? chunk.input.command : ""
  const output = loadedText ?? chunk.output ?? ""
  const pendingOutput = Boolean(chunk.loadOutput) && loadedText == null

  return (
    <div className="space-y-space-2">
      {command && (
        <pre className="cursor-text overflow-x-auto font-mono text-xxs leading-relaxed whitespace-pre text-primary select-text">
          <span className="text-tertiary">$ </span>
          {command}
        </pre>
      )}
      {output && <ToolResultBody value={output} />}
      {loadError && (
        <p className="text-xxs text-error-secondary">{loadError}</p>
      )}
      {!loadError && pendingOutput && (
        <p className="font-mono text-xxs text-secondary">
          {output ? "Loading the rest of the output…" : "Loading output…"}
        </p>
      )}
      {!output && !pendingOutput && chunk.status === "in_progress" && (
        <p className="font-mono text-xxs text-secondary">Running…</p>
      )}
      {!output && !pendingOutput && chunk.status === "pending" && (
        <p className="font-mono text-xxs text-warning-secondary">
          Waiting for approval…
        </p>
      )}
    </div>
  )
})
