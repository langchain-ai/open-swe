import { useMemo } from "react"

import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { formatJsonToolResult } from "./toolResultJson"

export function ToolResultBody({ value }: { value: string }) {
  const json = useMemo(() => formatJsonToolResult(value), [value])

  if (json !== null) return <CodeBlock text={json} language="json" />

  return (
    <pre className="max-h-64 cursor-text overflow-auto font-mono text-[12px] leading-relaxed break-words whitespace-pre-wrap text-muted-foreground select-text">
      {value}
    </pre>
  )
}
