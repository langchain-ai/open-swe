import { useMemo } from "react"

import { formatJsonToolResult } from "./toolResultJson"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"

const READ_STATUS_HEADER = /^@@ lines (\d+)-(\d+)(?: of \d+)?(?: \| .*)? @@$/

function formatReadFileResult(value: string): string {
  const lines = value.split("\n")
  const headerIndex = lines
    .slice(0, 3)
    .findIndex((line) => READ_STATUS_HEADER.test(line))
  if (headerIndex === -1) return value

  const header = READ_STATUS_HEADER.exec(lines[headerIndex] ?? "")
  if (!header) return value

  const notices = lines.slice(0, headerIndex)
  const source = lines.slice(headerIndex + 1)
  const startLine = Number(header[1])
  const width = String(startLine + source.length - 1).length
  return [
    ...notices,
    ...source.map(
      (line, index) => `${String(startLine + index).padStart(width)}  ${line}`
    ),
  ].join("\n")
}

export function ToolResultBody({ value }: { value: string }) {
  const formatted = useMemo(() => formatReadFileResult(value), [value])
  const json = useMemo(() => formatJsonToolResult(formatted), [formatted])

  if (json !== null) return <CodeBlock text={json} language="json" />

  return (
    <pre className="max-h-64 cursor-text overflow-auto font-mono text-[12px] leading-relaxed break-words whitespace-pre-wrap text-muted-foreground select-text">
      {formatted}
    </pre>
  )
}
