import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { ChatState } from "@langchain/macaw-components/ChatState"

import { cn } from "@/lib/utils"

const ACTIVITY_DWELL_MS = 550
const LABEL_TRANSITION_MS = 260

/** Paces rapid non-urgent live activity labels while passing urgent states through immediately. */
export function usePacedActivityLabel(
  label: string,
  urgent: boolean,
  active: boolean
): string {
  const [state, setState] = useState<{
    displayed: string
    locked: boolean
    wasActive: boolean
  }>(() => ({
    displayed: label,
    locked: active && !urgent,
    wasActive: active,
  }))
  const latestRef = useRef({ label, urgent })

  if (!active) {
    if (state.wasActive || state.locked || label !== state.displayed) {
      setState({ displayed: label, locked: false, wasActive: false })
    }
  } else if (!state.wasActive) {
    setState({ displayed: label, locked: !urgent, wasActive: true })
  } else if (label !== state.displayed && (urgent || !state.locked)) {
    setState({ displayed: label, locked: !urgent, wasActive: true })
  }

  useEffect(() => {
    latestRef.current = { label, urgent }
  }, [label, urgent])

  useEffect(() => {
    if (!active || !state.locked) return
    const timer = setTimeout(() => {
      const next = latestRef.current
      if (next.label !== state.displayed) {
        setState({
          displayed: next.label,
          locked: !next.urgent,
          wasActive: true,
        })
      } else {
        setState((prev) => (prev.locked ? { ...prev, locked: false } : prev))
      }
    }, ACTIVITY_DWELL_MS)
    return () => clearTimeout(timer)
  }, [active, state.displayed, state.locked])

  return active ? state.displayed : label
}

function ActivityLabel({ text }: { text: string }) {
  const [transition, setTransition] = useState<{
    current: string
    exiting: string | null
    seq: number
  }>({
    current: text,
    exiting: null,
    seq: 0,
  })
  const trackRef = useRef<HTMLSpanElement>(null)
  const lastWidthRef = useRef(0)
  const widthAnimRef = useRef<Animation | null>(null)

  if (text !== transition.current) {
    const reduced =
      typeof window !== "undefined" &&
      Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches)
    setTransition({
      current: text,
      exiting: reduced ? null : transition.current,
      seq: transition.seq + 1,
    })
  }

  useLayoutEffect(() => {
    const track = trackRef.current
    if (!track) return
    const running = widthAnimRef.current?.playState === "running"
    const prevWidth = running
      ? Math.ceil(track.getBoundingClientRect().width)
      : lastWidthRef.current
    widthAnimRef.current?.cancel()
    widthAnimRef.current = null
    const nextWidth = Math.ceil(track.getBoundingClientRect().width)
    lastWidthRef.current = nextWidth

    if (
      transition.exiting &&
      prevWidth > 0 &&
      nextWidth > 0 &&
      Math.abs(prevWidth - nextWidth) > 1 &&
      typeof track.animate === "function"
    ) {
      widthAnimRef.current = track.animate(
        [{ width: `${prevWidth}px` }, { width: `${nextWidth}px` }],
        {
          duration: LABEL_TRANSITION_MS,
          easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        }
      )
    }
  }, [transition.current, transition.exiting, transition.seq])

  useEffect(
    () => () => {
      widthAnimRef.current?.cancel()
    },
    []
  )

  useEffect(() => {
    if (!transition.exiting) return
    const timer = setTimeout(() => {
      setTransition((prev) => ({ ...prev, exiting: null }))
    }, LABEL_TRANSITION_MS)
    return () => clearTimeout(timer)
  }, [transition.exiting, transition.seq])

  return (
    <span
      ref={trackRef}
      className="activity-label-track relative inline-grid max-w-full min-w-0 overflow-hidden align-middle"
    >
      {transition.exiting && (
        <span
          key={`exit-${transition.seq}`}
          aria-hidden="true"
          className="activity-label-exit activity-label-exit-layer pointer-events-none overflow-hidden whitespace-nowrap text-secondary"
        >
          {transition.exiting}
        </span>
      )}
      <span
        key={transition.seq}
        className={cn(
          "shimmer-text block min-w-0",
          transition.exiting
            ? "activity-label-enter overflow-hidden whitespace-nowrap"
            : "truncate"
        )}
      >
        {transition.current}
      </span>
    </span>
  )
}

/** Animated live activity row built on Macaw's ChatState and elapsed timer. */
export function LiveActivityStatus({
  label,
  since,
  announce = true,
  className,
}: {
  label: string
  since?: number
  announce?: boolean
  className?: string
}) {
  return (
    <ChatState
      className={cn("min-w-0", className)}
      status="streaming"
      label={<ActivityLabel text={label} />}
      showElapsed
      startedAt={since}
      announce={announce}
    />
  )
}
