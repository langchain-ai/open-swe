import { memo } from "react"

import { parseEvalOutput } from "./evalOutput"
import { ToolResultBody } from "./ToolResultBody"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"

function stringInput(chunk: ToolExecutionChunk, key: string): string {
  const value = chunk.input?.[key]
  return typeof value === "string" ? value : ""
}

function OutputLabel({ children }: { children: string }) {
  return <p className="text-xxs font-medium text-tertiary">{children}</p>
}

function EvalOutputBody({ output }: { output: string }) {
  const parsed = parseEvalOutput(output)
  if (!parsed) return <ToolResultBody value={output} />

  const { stdout, result, error } = parsed
  const showResult = result && !(stdout && result.value === "undefined")

  return (
    <div className="space-y-space-2">
      {stdout && (
        <div className="space-y-space-1">
          <OutputLabel>Console</OutputLabel>
          <ToolResultBody value={stdout} />
        </div>
      )}
      {error && (
        <div className="space-y-space-1">
          <OutputLabel>{error.type || "Error"}</OutputLabel>
          <pre className="max-h-64 cursor-text overflow-auto font-mono text-xxs leading-relaxed break-words whitespace-pre-wrap text-error-secondary select-text">
            {error.message}
          </pre>
        </div>
      )}
      {showResult && (
        <div className="space-y-space-1">
          <OutputLabel>Result</OutputLabel>
          <ToolResultBody value={result.value} />
        </div>
      )}
    </div>
  )
}

/** Expanded body for calls that run something: a shell command or `eval` JavaScript. */
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
  const isCode = chunk.toolKind === "code"
  const command = stringInput(chunk, "command")
  const code = isCode ? stringInput(chunk, "code") : ""
  const output = loadedText ?? chunk.output ?? ""
  const pendingOutput = Boolean(chunk.loadOutput) && loadedText == null

  return (
    <div className="space-y-space-2">
      {code.trim() && (
        <div className="[&>div]:my-0">
          <CodeBlock text={code} language="javascript" />
        </div>
      )}
      {!isCode && command && (
        <pre className="cursor-text overflow-x-auto font-mono text-xxs leading-relaxed whitespace-pre text-primary select-text">
          <span className="text-tertiary">$ </span>
          {command}
        </pre>
      )}
      {output &&
        (isCode ? (
          <EvalOutputBody output={output} />
        ) : (
          <ToolResultBody value={output} />
        ))}
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
