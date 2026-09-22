import type { Chunk } from "@/features/agents/lib/types"
import type { ApprovalCallbacks } from "./types"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { ToolExecution } from "@/features/agents/components/chat/ToolExecution"
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
      return (
        <div className="text-foreground">
          <Markdown content={chunk.text} isLive={isMarkdownLive} />
        </div>
      )
    case "code":
      return <CodeBlock text={chunk.text} language={chunk.language} />
    case "error":
      return <span className="text-destructive">{chunk.text}</span>
    case "list":
      return (
        <div className="ml-2 text-muted-foreground">
          {chunk.lines.map((line, i) => (
            <div key={i}>- {line}</div>
          ))}
        </div>
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
          className="max-h-48 max-w-48 rounded border border-border"
        />
      )
  }
}
