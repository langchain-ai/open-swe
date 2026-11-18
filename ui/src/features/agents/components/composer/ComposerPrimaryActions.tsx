import { ChatComposerAction } from "@langchain/macaw-components/ChatComposer"
import { useEffect, useRef, useState } from "react"

import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useOptionalThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

export interface ActiveRun {
  threadId: string
  /** Server-reported run state, independent of this client's event stream. */
  running: boolean
}

export interface ComposerRunOptions {
  canSubmit: boolean
  submitting: boolean
  /** Enables the stop action for the thread's live run. */
  activeRun?: ActiveRun
  /**
   * Stop handler. Required outside a thread stream (desktop ACP, or before
   * LangGraph assigns a thread id); inside one it replaces the source's stop.
   */
  onStop?: () => void | Promise<void>
  /** False when this viewer may not stop the live run. */
  stoppable?: boolean
  /** Set false while the composer owns Escape (an open command menu or model picker). */
  stopOnEscape?: boolean
  /** Label of the send button while a run is live. */
  runningLabel?: string
}

export interface ComposerRun {
  composer:
    | { action: "send" | "sending"; onStop?: never }
    | { action: "stopping"; onStop?: never }
    | { action: "stop"; onStop: () => void }
  label?: string
  stopping: boolean
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

export function useComposerRun({
  canSubmit,
  submitting,
  activeRun,
  onStop,
  stoppable,
  stopOnEscape = true,
  runningLabel = "Queue message",
}: ComposerRunOptions): ComposerRun {
  const source = useOptionalThreadSource()
  const threadSource = useIsInAgentThreadStream() ? source : null
  const [stopping, setStopping] = useState(false)

  const running =
    stoppable !== false &&
    (threadSource
      ? Boolean(submitting || threadSource.isRunning || activeRun?.running)
      : Boolean((submitting || activeRun?.running) && onStop))

  const stop = async () => {
    if (stopping) return
    setStopping(true)
    try {
      // The view may wrap the stop, e.g. to return queued follow-ups to the
      // composer before the run is interrupted.
      await (onStop ? onStop() : threadSource?.stop())
    } finally {
      setStopping(false)
    }
  }

  useEscapeToStop(running && !stopping && stopOnEscape, () => void stop())

  if (stopping)
    return { composer: { action: "stopping" }, label: "Stopping run", stopping }

  if (!running)
    return {
      composer: { action: submitting ? "sending" : "send" },
      stopping,
    }
  if (canSubmit)
    return { composer: { action: "send" }, label: runningLabel, stopping }
  return {
    composer: { action: "stop", onStop: () => void stop() },
    label: "Stop run",
    stopping,
  }
}

export function ComposerPrimaryAction({ run }: { run: ComposerRun }) {
  return (
    <ChatComposerAction
      label={run.label}
      tooltipProps={
        run.composer.action === "stop" ? { title: "Stop run (Esc)" } : undefined
      }
    />
  )
}
