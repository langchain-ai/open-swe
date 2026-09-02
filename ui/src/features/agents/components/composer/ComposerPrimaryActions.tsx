import { useEffect, useRef, useState } from "react"
import { LoaderCircle } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"

import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  useCancelAgentThread,
} from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

export interface ActiveRun {
  threadId: string
  /** Server-reported run state, independent of this client's event stream. */
  running: boolean
}

export interface ComposerPrimaryActionsProps {
  canSubmit: boolean
  submitting: boolean
  onSubmit: () => void
  /** Enables the stop button for the thread's live run. */
  activeRun?: ActiveRun
  /** Direct stop handler for desktop ACP or before LangGraph assigns a thread id. */
  onStop?: () => void | Promise<void>
  /** Set false while the composer owns Escape (an open command menu or model picker). */
  stopOnEscape?: boolean
}

function useEscapeToStop(enabled: boolean, onStop: () => void) {
  const onStopRef = useRef(onStop)
  useEffect(() => {
    onStopRef.current = onStop
  })

  useEffect(() => {
    if (!enabled) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing)
        return
      // Escape belongs to whatever overlay is open and focused; only a bare
      // Escape on the page reaches the run.
      const target = event.target
      if (
        target instanceof Element &&
        target.closest(
          '[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"]'
        )
      )
        return
      event.preventDefault()
      onStopRef.current()
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [enabled])
}

function SendIcon() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="14"
      viewBox="0 0 14 14"
      width="14"
    >
      <path
        d="M7 11.5V2.5M7 2.5L3 6.5M7 2.5L11 6.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function SendButton({
  canSubmit,
  submitting,
  onSubmit,
  label = "Send message",
}: ComposerPrimaryActionsProps & { label?: string }) {
  return (
    <button
      aria-label={label}
      className={cn(
        "relative isolate flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-foreground text-background shadow-xs shadow-foreground/20 transition-all duration-150",
        "hover:scale-105 hover:bg-foreground/85 active:shadow-none enabled:cursor-pointer",
        "disabled:pointer-events-none disabled:opacity-30 disabled:shadow-none"
      )}
      disabled={!canSubmit}
      onClick={onSubmit}
      type="button"
    >
      {submitting ? (
        <LoaderCircle className="size-3.5 animate-spin" />
      ) : (
        <SendIcon />
      )}
    </button>
  )
}

function StopButton({
  disabled,
  stopOnEscape = true,
  onStop,
}: {
  disabled: boolean
  stopOnEscape?: boolean
  onStop: () => void
}) {
  useEscapeToStop(stopOnEscape && !disabled, onStop)

  return (
    <button
      aria-label="Stop run"
      className={cn(
        "flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full bg-foreground text-background shadow-xs shadow-foreground/20 transition-all duration-150",
        "hover:scale-105 hover:bg-foreground/85 active:shadow-none",
        "disabled:pointer-events-none disabled:opacity-40"
      )}
      disabled={disabled}
      onClick={onStop}
      title="Stop run (Esc)"
      type="button"
    >
      {disabled ? (
        <LoaderCircle className="size-3.5 animate-spin" />
      ) : (
        <svg
          aria-hidden="true"
          fill="currentColor"
          height="12"
          viewBox="0 0 12 12"
          width="12"
        >
          <rect height="8" rx="1.5" width="8" x="2" y="2" />
        </svg>
      )}
    </button>
  )
}

function StreamPrimaryActions(props: ComposerPrimaryActionsProps) {
  const stream = useAgentThreadRuntime()
  const queryClient = useQueryClient()
  const [stopping, setStopping] = useState(false)
  const threadId = props.activeRun?.threadId ?? stream.threadId ?? ""
  const cancelThread = useCancelAgentThread(threadId)

  const handleStop = async () => {
    if (stopping) return
    setStopping(true)
    try {
      // `stream.stop()` only cancels server-side when this client dispatched the
      // run, so cancel by thread first: a run started from Slack/Linear/GitHub
      // (or joined after a reload) has no client-side run id to cancel.
      let cancelledThread
      if (threadId) {
        try {
          cancelledThread = await cancelThread.mutateAsync()
        } catch {
          // Cancellation failed (transient 5xx, or a non-owner viewer). Leave
          // the stream and the thread's status polling untouched: presenting a
          // stopped state here would strand the UI on a still-running run.
          return
        }
      }
      await stream.disconnect()
      if (threadId && cancelledThread?.status !== "running") {
        queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
          prev ? { ...prev, status: "interrupted" as const } : prev
        )
        invalidateAgentThreadLists(queryClient)
      }
    } finally {
      setStopping(false)
    }
  }

  const running =
    props.submitting || stream.isLoading || props.activeRun?.running
  useEscapeToStop(
    Boolean(running && props.canSubmit && props.stopOnEscape !== false),
    () => void handleStop()
  )

  // Server truth (`activeRun.running`) matters as much as the client stream:
  // this browser only sees `isLoading` once it observes a lifecycle event, so a
  // run it never joined would otherwise render an unusable send button.
  if (!running) return <SendButton {...props} />

  return props.canSubmit ? (
    <SendButton {...props} canSubmit={!stopping} label="Steer agent" />
  ) : (
    <StopButton
      disabled={stopping}
      onStop={() => void handleStop()}
      stopOnEscape={props.stopOnEscape}
    />
  )
}

function DirectPrimaryActions(props: ComposerPrimaryActionsProps) {
  const [stopping, setStopping] = useState(false)
  const running = Boolean(
    (props.submitting || props.activeRun?.running) && props.onStop
  )
  const stop = async () => {
    if (stopping) return
    setStopping(true)
    try {
      await props.onStop?.()
    } finally {
      setStopping(false)
    }
  }
  useEscapeToStop(
    Boolean(running && props.canSubmit && props.stopOnEscape !== false),
    () => void stop()
  )
  if (!running) return <SendButton {...props} />
  return props.canSubmit ? (
    <SendButton {...props} canSubmit={!stopping} label="Steer agent" />
  ) : (
    <StopButton
      disabled={stopping}
      onStop={() => void stop()}
      stopOnEscape={props.stopOnEscape}
    />
  )
}

/** The composer's send button, which becomes a stop button while a run is live. */
export function ComposerPrimaryActions(props: ComposerPrimaryActionsProps) {
  const inAgentThreadStream = useIsInAgentThreadStream()
  if (inAgentThreadStream) return <StreamPrimaryActions {...props} />
  return <DirectPrimaryActions {...props} />
}
