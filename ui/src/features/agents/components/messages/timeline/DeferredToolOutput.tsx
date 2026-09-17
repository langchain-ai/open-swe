import { createContext, useContext, useEffect, useState } from "react"

import { ToolResultBody } from "./ToolResultBody"
import type { ReactNode } from "react"
import { agentsApi } from "@/features/agents/lib/api"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

/**
 * The transcript view of a thread ships tool calls without their results.
 * Rows that expand a deferred result fetch it here, by thread and tool call id.
 */
const DeferredToolResultsContext = createContext<string | null>(null)

export function DeferredToolResultsProvider({
  threadId,
  children,
}: {
  threadId: string | null | undefined
  children: ReactNode
}) {
  return (
    <DeferredToolResultsContext.Provider value={threadId ?? null}>
      {children}
    </DeferredToolResultsContext.Provider>
  )
}

function resultText(content: unknown): string {
  if (typeof content === "string") return content
  if (Array.isArray(content)) {
    return content
      .map((block: unknown) =>
        typeof block === "object" &&
        block !== null &&
        "text" in block &&
        typeof block.text === "string"
          ? block.text
          : ""
      )
      .join("")
  }
  try {
    return JSON.stringify(content)
  } catch {
    return String(content)
  }
}

type LoadState =
  | { status: "loading" }
  | { status: "loaded"; text: string }
  | { status: "error"; message: string }

export function DeferredToolOutput({ chunk }: { chunk: ToolExecutionChunk }) {
  const threadId = useContext(DeferredToolResultsContext)
  const [state, setState] = useState<LoadState>({ status: "loading" })

  useEffect(() => {
    if (!threadId) return
    let active = true
    // oxlint-disable-next-line react/set-state-in-effect
    setState({ status: "loading" })
    agentsApi
      .getThreadToolResults(threadId, [chunk.toolCallId])
      .then((results) => {
        if (!active) return
        const found = results[chunk.toolCallId]
        setState({
          status: "loaded",
          text: found ? resultText(found.content).trim() : "",
        })
      })
      .catch((error: unknown) => {
        if (!active) return
        setState({
          status: "error",
          message: error instanceof Error ? error.message : String(error),
        })
      })
    return () => {
      active = false
    }
  }, [chunk.toolCallId, threadId])

  if (!threadId) return null
  if (state.status === "loading") {
    return (
      <p className="font-mono text-[12px] text-muted-foreground">
        Loading result…
      </p>
    )
  }
  if (state.status === "error") {
    return (
      <p className="font-mono text-[12px] text-destructive">
        Could not load result: {state.message}
      </p>
    )
  }
  if (!state.text) {
    return (
      <p className="font-mono text-[12px] text-muted-foreground">
        No output.
      </p>
    )
  }
  return <ToolResultBody value={state.text} />
}
