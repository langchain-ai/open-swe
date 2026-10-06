import type { Chunk } from "@/features/agents/lib/types"
import type { ApprovalCallbacks } from "./types"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { ToolExecution } from "@/features/agents/components/chat/ToolExecution"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"
import { AlertTriangle } from "@/components/glyphs"
import { MessageImage } from "./MessageImage"

export function ChunkRenderer({
  chunk,
  repoPath,
  isMarkdownLive,
  ...callbacks
}: {
  chunk: Chunk
  repoPath?: string
  isMarkdownLive?: boolean
} & ApprovalCallbacks) {
  switch (chunk.kind) {
    case "text":
      return <Markdown content={chunk.text} isLive={isMarkdownLive} />
    case "code":
      return <CodeBlock text={chunk.text} language={chunk.language} />
    case "error":
      return (
        <Alert tone="risk" icon={AlertTriangle}>
          <AlertDescription className="wrap-anywhere whitespace-pre-wrap">
            {chunk.text}
          </AlertDescription>
        </Alert>
      )
    case "list":
      return (
        <Box
          render={<ul />}
          className="list-disc pl-5 text-body text-ink-subtle marker:text-ink-subtle"
        >
          {chunk.lines.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </Box>
      )
    case "tool-execution":
      return (
        <ToolExecution
          chunk={chunk}
          repoPath={repoPath}
          onApprove={callbacks.onApprove}
          onReject={callbacks.onReject}
          onAutoApprove={callbacks.onAutoApprove}
        />
      )
    case "image":
      return (
        <MessageImage
          chunk={chunk}
          className="max-h-48 max-w-48 rounded-compact border border-line"
        />
      )
  }
}
