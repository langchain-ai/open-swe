import { memo } from "react"

import { ToolResultBody } from "./ToolResultBody"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

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
    <Stack gap="sm">
      {command && (
        <pre className="cursor-text overflow-x-auto font-mono text-meta whitespace-pre text-ink select-text">
          <span className="text-ink-subtle">$ </span>
          {command}
        </pre>
      )}
      {output && <ToolResultBody value={output} />}
      {loadError && (
        <Box render={<p />} className="text-meta text-risk">
          {loadError}
        </Box>
      )}
      {!loadError && pendingOutput && (
        <Inline gap="xs" align="center" className="text-meta text-ink-subtle">
          <Spinner size="sm" />
          {output ? "Loading the rest of the output…" : "Loading output…"}
        </Inline>
      )}
      {!output && !pendingOutput && chunk.status === "in_progress" && (
        <Box render={<p />} className="shimmer-text text-meta">
          Running…
        </Box>
      )}
      {!output && !pendingOutput && chunk.status === "pending" && (
        <Box render={<p />} className="text-meta text-attention">
          Waiting for approval…
        </Box>
      )}
    </Stack>
  )
})
