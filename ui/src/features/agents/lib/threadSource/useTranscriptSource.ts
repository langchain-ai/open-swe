import { useCallback, useEffect, useMemo, useState } from "react"

import { RunTracker } from "@/lib/perf/streaming"
import {
  runStartCommand,
  startRun as postRunStart,
} from "@/features/agents/lib/transcript/api"
import { subagentToolCalls } from "@/features/agents/lib/transcript/reducer"
import { useThreadTranscript } from "@/features/agents/lib/transcript/useThreadTranscript"
import { useCancelRun } from "./useCancelRun"
import type { SubagentToolCall } from "@/features/agents/lib/transcript/reducer"
import type { ThreadRunInput, TranscriptThreadSource } from "./types"

/** The append-only transcript log, behind the source interface. */
export function useTranscriptSource(threadId: string): TranscriptThreadSource {
  const [runTracker] = useState(
    () => new RunTracker({ transport: "cloud", threadId })
  )
  useEffect(() => () => runTracker.dispose(), [runTracker])
  const transcript = useThreadTranscript(threadId, { runTracker })
  const stop = useCancelRun(threadId)

  // `turn.started` lands seconds after the command is accepted, and until it
  // does the transcript still reads as idle — long enough for a second
  // message to be sent as a new run instead of being queued behind this one.
  const [pendingRun, setPendingRun] = useState(false)
  const startRun = useCallback(
    async ({ message, configurable }: ThreadRunInput) => {
      runTracker.submitted()
      try {
        await postRunStart(
          threadId,
          runStartCommand({ threadId, message, configurable })
        )
      } catch (error) {
        setPendingRun(false)
        throw error
      }
      setPendingRun(true)
      runTracker.created()
    },
    [runTracker, threadId]
  )

  // Adjusted during render rather than from an effect: the log has taken over
  // once the run is visibly running, and a thread that went to `error` never
  // will — either way the guess is spent, and waiting a commit to say so
  // would leave one render claiming a run that already reported itself.
  const status = transcript.state?.status ?? null
  if (pendingRun && (transcript.isRunning || status === "error")) {
    setPendingRun(false)
  }

  const state = transcript.state
  const contextTokens = state?.contextTokens ?? null
  const subagents = useCallback(
    (namespace: ReadonlyArray<string>): Array<SubagentToolCall> =>
      state ? subagentToolCalls(state, namespace) : [],
    [state]
  )

  return useMemo(
    () => ({
      kind: "transcript",
      threadId,
      messages: transcript.messages,
      isRunning: transcript.isRunning || pendingRun,
      isHydrating: transcript.isHydrating,
      hydration: transcript.hydration,
      error: transcript.error,
      isOffloading: transcript.isOffloading,
      routed: transcript.routed,
      connection: transcript.connection,
      contextTokens,
      subagentToolCalls: subagents,
      startRun,
      stop,
      hasOlder: transcript.hasOlder,
      isLoadingOlder: transcript.isLoadingOlder,
      loadOlder: transcript.loadOlder,
    }),
    [contextTokens, pendingRun, startRun, stop, subagents, threadId, transcript]
  )
}
