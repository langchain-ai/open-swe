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

  const startRun = useCallback(
    async ({ message, configurable }: ThreadRunInput) => {
      runTracker.submitted()
      await postRunStart(
        threadId,
        runStartCommand({ threadId, message, configurable })
      )
      runTracker.created()
    },
    [runTracker, threadId]
  )

  const state = transcript.state
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
      isRunning: transcript.isRunning,
      isHydrating: transcript.isHydrating,
      hydration: transcript.hydration,
      error: transcript.error,
      isOffloading: transcript.isOffloading,
      routed: transcript.routed,
      connection: transcript.connection,
      // The log records no token usage, so the composer's context meter has no
      // source here.
      contextTokens: null,
      subagentToolCalls: subagents,
      startRun,
      stop,
    }),
    [startRun, stop, subagents, threadId, transcript]
  )
}
