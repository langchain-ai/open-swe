import { memo } from "react"

import { MessageImage } from "../MessageImage"
import { ToolResultBody } from "./ToolResultBody"
import type { ReactNode } from "react"
import { formatShellCommand } from "@/features/agents/components/chat/toolExecutionDisplay"
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

interface EntryBodyProps {
  chunk: ToolExecutionChunk
  /** Full output fetched on expand; the chunk alone holds only a preview. */
  loadedText?: string | null
  loadError?: string | null
}

/** A call's input, then its output and loading state. */
export function ToolCallBody({
  chunk,
  loadedText,
  loadError,
  input,
}: EntryBodyProps & { input: ReactNode }) {
  const output = loadedText ?? chunk.output ?? ""
  const pendingOutput = Boolean(chunk.loadOutput) && loadedText == null

  return (
    <div className="space-y-space-2">
      {input}
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
}

export const ShellEntryBody = memo(function ShellEntryBody(
  props: EntryBodyProps
) {
  const command =
    typeof props.chunk.input?.command === "string"
      ? formatShellCommand(props.chunk.input.command)
      : ""

  return (
    <ToolCallBody
      {...props}
      input={
        command && (
          <pre className="cursor-text overflow-x-auto font-mono text-xxs leading-relaxed whitespace-pre text-primary select-text">
            <span className="text-tertiary">$ </span>
            {command}
          </pre>
        )
      }
    />
  )
})

/** A call's arguments as formatted JSON, then its output. */
export const JsonInputEntryBody = memo(function JsonInputEntryBody(
  props: EntryBodyProps
) {
  const { input } = props.chunk

  return (
    <ToolCallBody
      {...props}
      input={
        input &&
        Object.keys(input).length > 0 && (
          <ToolResultBody value={JSON.stringify(input)} />
        )
      }
    />
  )
})
