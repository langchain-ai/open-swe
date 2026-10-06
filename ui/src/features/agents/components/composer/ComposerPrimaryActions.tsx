import { useEffect, useRef, useState } from "react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ArrowUp, Square } from "@/components/glyphs"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

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
  /**
   * Stop handler. Required outside a thread stream (desktop ACP, or before
   * LangGraph assigns a thread id); inside one it replaces the source's stop.
   */
  onStop?: () => void | Promise<void>
  /** Set false while the composer owns Escape (an open command menu or model picker). */
  stopOnEscape?: boolean
  /** Label of the send button while a run is live. */
  runningLabel?: string
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

/* The composer's one primary: a round send on the control rung, as in the reference composer. */
const PRIMARY_ACTION_CLASS = "rounded-full"

function SendButton({
  canSubmit,
  submitting,
  onSubmit,
  label = "Send message",
}: ComposerPrimaryActionsProps & { label?: string }) {
  return (
    <Button
      aria-label={label}
      className={PRIMARY_ACTION_CLASS}
      disabled={!canSubmit}
      loading={submitting}
      onClick={onSubmit}
      size="icon"
      type="button"
    >
      <Icon icon={ArrowUp} />
    </Button>
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
    <Button
      aria-label="Stop run"
      className={PRIMARY_ACTION_CLASS}
      loading={disabled}
      onClick={onStop}
      size="icon"
      title="Stop run (Esc)"
      type="button"
    >
      <Icon icon={Square} size="sm" />
    </Button>
  )
}

function ThreadPrimaryActions(props: ComposerPrimaryActionsProps) {
  const source = useThreadSource()
  const [stopping, setStopping] = useState(false)

  const handleStop = async () => {
    if (stopping) return
    setStopping(true)
    try {
      // The view may wrap the stop, e.g. to return queued follow-ups to the
      // composer before the run is interrupted.
      await (props.onStop ? props.onStop() : source.stop())
    } finally {
      setStopping(false)
    }
  }

  const running =
    props.submitting || source.isRunning || props.activeRun?.running
  useEscapeToStop(
    Boolean(running && props.canSubmit && props.stopOnEscape !== false),
    () => void handleStop()
  )

  // Server truth (`activeRun.running`) matters as much as the client's view of
  // the run: this browser only sees a run it observed events for, so a run it
  // never joined would otherwise render an unusable send button.
  if (!running) return <SendButton {...props} />

  return props.canSubmit ? (
    <SendButton
      {...props}
      canSubmit={!stopping}
      label={props.runningLabel ?? "Queue message"}
    />
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
    <SendButton
      {...props}
      canSubmit={!stopping}
      label={props.runningLabel ?? "Queue message"}
    />
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
  if (inAgentThreadStream) return <ThreadPrimaryActions {...props} />
  return <DirectPrimaryActions {...props} />
}
