import { memo, useMemo } from "react"

import { ToolResultBody } from "./ToolResultBody"
import { parseEvalOutput } from "./evalOutput"
import type { ReactNode } from "react"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"

interface OutputDetail {
  chunk: ToolExecutionChunk
  /** Full output fetched on expand; the chunk alone holds only a preview. */
  loadedText?: string | null
  loadError?: string | null
}

function inputString(chunk: ToolExecutionChunk, key: string): string {
  const value = chunk.input?.[key]
  return typeof value === "string" ? value : ""
}

/** Load, run and approval states shared by bodies that render a call's output. */
function OutputStatus({
  chunk,
  loadedText,
  loadError,
  hasOutput,
}: OutputDetail & { hasOutput: boolean }) {
  const pendingOutput = Boolean(chunk.loadOutput) && loadedText == null
  if (loadError) {
    return <p className="text-xxs text-error-secondary">{loadError}</p>
  }
  if (pendingOutput) {
    return (
      <p className="font-mono text-xxs text-secondary">
        {hasOutput ? "Loading the rest of the output…" : "Loading output…"}
      </p>
    )
  }
  if (hasOutput) return null
  if (chunk.status === "in_progress") {
    return <p className="font-mono text-xxs text-secondary">Running…</p>
  }
  if (chunk.status === "pending") {
    return (
      <p className="font-mono text-xxs text-warning-secondary">
        Waiting for approval…
      </p>
    )
  }
  return null
}

export const ShellEntryBody = memo(function ShellEntryBody(
  detail: OutputDetail
) {
  const command = inputString(detail.chunk, "command")
  const output = detail.loadedText ?? detail.chunk.output ?? ""

  return (
    <div className="space-y-space-2">
      {command && (
        <pre className="cursor-text overflow-x-auto font-mono text-xxs leading-relaxed whitespace-pre text-primary select-text">
          <span className="text-tertiary">$ </span>
          {command}
        </pre>
      )}
      {output && <ToolResultBody value={output} />}
      <OutputStatus {...detail} hasOutput={Boolean(output)} />
    </div>
  )
})

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="space-y-space-1">
      <h4 className="font-mono text-xxs text-tertiary select-none">{label}</h4>
      {children}
    </section>
  )
}

/** A code-mode `eval` call: its code, the tool calls it made, then what it logged and returned. */
export function CodeEntryBody({
  calls,
  callCount,
  ...detail
}: OutputDetail & {
  /** The nested calls, rendered; null when the code called no tools. */
  calls: ReactNode
  callCount: number
}) {
  const code = inputString(detail.chunk, "code")
  const output = detail.loadedText ?? detail.chunk.output ?? ""
  const parsed = useMemo(() => parseEvalOutput(output), [output])
  const result =
    parsed?.result != null &&
    (parsed.result !== "undefined" || (!parsed.stdout && calls == null))
      ? parsed.result
      : null

  return (
    <div className="space-y-space-2">
      {code && <CodeBlock text={code} language="javascript" />}
      {calls != null && (
        <Section label={`${callCount} tool call${callCount === 1 ? "" : "s"}`}>
          <div>{calls}</div>
        </Section>
      )}
      {parsed?.stdout && (
        <Section label="Console">
          <pre className="max-h-64 cursor-text overflow-auto font-mono text-xxs leading-relaxed break-words whitespace-pre-wrap text-secondary select-text">
            {parsed.stdout}
          </pre>
        </Section>
      )}
      {parsed?.error && (
        <Section label={parsed.error.type}>
          <pre className="max-h-64 cursor-text overflow-auto font-mono text-xxs leading-relaxed break-words whitespace-pre-wrap text-error-secondary select-text">
            {parsed.error.message}
          </pre>
        </Section>
      )}
      {result != null && (
        <Section label="Result">
          <ToolResultBody value={result} />
        </Section>
      )}
      {!parsed && output && <ToolResultBody value={output} />}
      <OutputStatus {...detail} hasOutput={Boolean(output)} />
    </div>
  )
}
