import type { ImageChunk, TextChunk } from "@/features/agents/lib/types"
import { Markdown } from "@/features/agents/components/chat/Markdown"

/** Renders the prose side of an agent turn: markdown text or an inline image. */
export function ChunkRenderer({
  chunk,
  isMarkdownLive,
}: {
  chunk: TextChunk | ImageChunk
  isMarkdownLive?: boolean
}) {
  if (chunk.kind === "image") {
    return (
      <img
        src={`data:${chunk.mimeType};base64,${chunk.base64}`}
        alt={chunk.fileName || "image"}
        className="max-h-48 max-w-48 rounded border border-border"
      />
    )
  }
  return (
    <div className="text-foreground">
      <Markdown content={chunk.text} isLive={isMarkdownLive} />
    </div>
  )
}
