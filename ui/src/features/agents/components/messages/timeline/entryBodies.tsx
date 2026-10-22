import { memo } from "react"

import { MessageImage } from "../MessageImage"
import { ToolResultBody } from "./ToolResultBody"
import type {
  AnyImageChunk,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"

/** A tool's images above its text, bounded like a message's attachments. */
export const ImageEntryBody = memo(function ImageEntryBody({
  images,
  text,
}: {
  images: Array<AnyImageChunk>
  text: string | null
}) {
  return (
    <div className="space-y-space-2">
      {images.map((image, index) => (
        <MessageImage
          key={index}
          chunk={image}
          className="block h-auto max-h-[220px] w-auto max-w-[min(100%,420px)] rounded-lg border border-default bg-surface-level-1 object-contain"
        />
      ))}
      {text && <ToolResultBody value={text} />}
    </div>
  )
})

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
