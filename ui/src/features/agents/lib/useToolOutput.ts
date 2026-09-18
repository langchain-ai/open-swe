import { useEffect, useState } from "react"

import type { ToolExecutionChunk } from "./types"

/**
 * The tool call's full output. A chunk from the transcript snapshot carries a
 * preview plus `loadOutput`; renderers that parse the whole output (a SQL
 * result table) need the rest before they can show anything faithful.
 */
export function useToolOutput(chunk: ToolExecutionChunk): string | undefined {
  const { loadOutput, output } = chunk
  const [loaded, setLoaded] = useState<{
    load: typeof loadOutput
    text: string
  } | null>(null)

  useEffect(() => {
    if (!loadOutput) return
    let active = true
    loadOutput().then(
      (text) => {
        if (active) setLoaded({ load: loadOutput, text })
      },
      () => {
        // The preview stays up; the row's expand path reports load failures.
      }
    )
    return () => {
      active = false
    }
  }, [loadOutput])

  return loaded && loaded.load === loadOutput ? loaded.text : output
}
