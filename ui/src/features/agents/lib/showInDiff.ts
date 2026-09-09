/**
 * The `show_in_diff` tool: the agent asks the diff the user is reading to scroll
 * to a file, and optionally a line inside it. The request rides on the tool
 * message's artifact, so the transcript stream is the whole transport.
 */
import { useEffect, useRef } from "react"
import { ToolMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"

export type DiffSide = "old" | "new"

export interface ShowInDiffTarget {
  path: string
  line: number | null
  side: DiffSide
}

export function parseShowInDiffArtifact(
  artifact: unknown
): ShowInDiffTarget | null {
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) {
    return null
  }
  const value = artifact as Record<string, unknown>
  if (value.type !== "show_in_diff" || typeof value.path !== "string") {
    return null
  }
  const path = value.path.trim()
  if (!path) return null
  const line =
    typeof value.line === "number" && Number.isFinite(value.line)
      ? Math.max(1, Math.trunc(value.line))
      : null
  return { path, line, side: value.side === "old" ? "old" : "new" }
}

/**
 * Call `onShow` for each navigation the agent asks for as it streams in.
 * Requests already in the transcript when the conversation loads are consumed
 * silently: reopening a thread must not yank the diff to wherever the last
 * answer pointed. Pass `ready` as false while the thread is still hydrating so
 * its history lands in that same silent pass.
 */
export function useShowInDiffRequests(
  messages: ReadonlyArray<BaseMessage>,
  ready: boolean,
  onShow: (target: ShowInDiffTarget) => void
): void {
  const seenRef = useRef(new Set<string>())
  const primedRef = useRef(false)
  const onShowRef = useRef(onShow)
  useEffect(() => {
    onShowRef.current = onShow
  }, [onShow])

  useEffect(() => {
    const requests: Array<{ key: string; value: ShowInDiffTarget }> = []
    for (const message of messages) {
      if (!ToolMessage.isInstance(message)) continue
      const key = message.tool_call_id || message.id
      if (!key) continue
      const value = parseShowInDiffArtifact(message.artifact)
      if (value) requests.push({ key, value })
    }
    const seen = seenRef.current
    const fresh = primedRef.current
      ? requests.filter((request) => !seen.has(request.key))
      : []
    for (const request of requests) seen.add(request.key)
    if (ready) primedRef.current = true
    const last = fresh.at(-1)
    if (last) onShowRef.current(last.value)
  }, [messages, ready])
}
