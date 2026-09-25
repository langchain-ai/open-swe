import { useCallback, useEffect, useMemo, useState } from "react"

import { RunTracker } from "@/lib/perf/streaming"
import {
  runStartCommand,
  startRun as postRunStart,
} from "@/features/agents/lib/transcript/api"
import {
  subagentMessages,
  subagentTask,
  subagentToolCalls,
} from "@/features/agents/lib/transcript/reducer"
import { useThreadTranscript } from "@/features/agents/lib/transcript/useThreadTranscript"
import { useCancelRun } from "./useCancelRun"
import type {
  SubagentToolCall,
  TranscriptToolCallState,
} from "@/features/agents/lib/transcript/reducer"
import type { Message } from "@/features/agents/lib/types"
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
    async ({ message, configurable, enqueue }: ThreadRunInput) => {
      runTracker.submitted()
      await postRunStart(
        threadId,
        runStartCommand({ threadId, message, configurable, enqueue })
      )
      runTracker.created()
    },
    [runTracker, threadId]
  )

  const state = transcript.state
  const contextTokens = state?.contextTokens ?? null
  const subagents = useCallback(
    (namespace: ReadonlyArray<string>): Array<SubagentToolCall> =>
      state ? subagentToolCalls(state, namespace) : [],
    [state]
  )
  const subagentTranscript = useCallback(
    (namespace: ReadonlyArray<string>): Array<Message> =>
      state ? subagentMessages(state, namespace) : [],
    [state]
  )
  const task = useCallback(
    (toolCallId: string): TranscriptToolCallState | null =>
      state ? subagentTask(state, toolCallId) : null,
    [state]
  )

  return useMemo(
    () => ({
      kind: "transcript",
      threadId,
      messages: transcript.messages,
      queued: transcript.queued,
      isRunning: transcript.isRunning,
      isHydrating: transcript.isHydrating,
      hydration: transcript.hydration,
      error: transcript.error,
      isOffloading: transcript.isOffloading,
      routed: transcript.routed,
      connection: transcript.connection,
      contextTokens,
      subagentToolCalls: subagents,
      subagentMessages: subagentTranscript,
      subagentTask: task,
      startRun,
      stop,
      hasOlder: transcript.hasOlder,
      isLoadingOlder: transcript.isLoadingOlder,
      loadOlder: transcript.loadOlder,
    }),
    [
      contextTokens,
      startRun,
      stop,
      subagents,
      subagentTranscript,
      task,
      threadId,
      transcript,
    ]
  )
}
