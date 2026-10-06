import { useMemo } from "react"

import { formatJsonToolResult } from "./toolResultJson"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"

export function ToolResultBody({ value }: { value: string }) {
  const json = useMemo(() => formatJsonToolResult(value), [value])

  if (json !== null) return <CodeBlock text={json} language="json" />

  return (
    <pre className="max-h-64 cursor-text overflow-auto rounded-compact border border-line bg-muted px-3 py-2.5 font-mono text-meta break-words whitespace-pre-wrap text-ink-muted select-text">
      {value}
    </pre>
  )
}
