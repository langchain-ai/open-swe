import { useEffect, useMemo, useState } from "react"

import { formatJsonToolResult } from "./toolResultJson"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"

/** Lazily loads a tool call's full output once its disclosure is opened. */
export function useLazyOutput(
  loader: (() => Promise<string>) | null | undefined,
  expanded: boolean
): { loadedText: string | null; loadError: string | null } {
  const [result, setResult] = useState<{
    loader: (() => Promise<string>) | null | undefined
    loadedText: string | null
    loadError: string | null
  }>({ loader, loadedText: null, loadError: null })

  const current =
    result.loader === loader
      ? result
      : { loader, loadedText: null, loadError: null }

  useEffect(() => {
    if (!expanded || !loader) return
    if (current.loadedText !== null || current.loadError !== null) return
    let active = true
    void loader().then(
      (text) => {
        if (active) setResult({ loader, loadedText: text, loadError: null })
      },
      (error: unknown) => {
        if (!active) return
        setResult({
          loader,
          loadedText: null,
          loadError:
            error instanceof Error ? error.message : "Could not load output",
        })
      }
    )
    return () => {
      active = false
    }
  }, [current.loadError, current.loadedText, expanded, loader])

  return { loadedText: current.loadedText, loadError: current.loadError }
}

export function ToolResultBody({ value }: { value: string }) {
  const json = useMemo(() => formatJsonToolResult(value), [value])

  if (json !== null) return <CodeBlock text={json} language="json" />

  return (
    <pre className="max-h-64 cursor-text overflow-auto font-mono text-xxs leading-relaxed break-words whitespace-pre-wrap text-secondary select-text">
      {value}
    </pre>
  )
}
