import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Slider } from "@langchain/macaw-components/Slider"
import { useEffect, useRef, useState, type PointerEvent } from "react"

import { evidenceMediaUrl, type FrontendScenario } from "./api"
import { ComparisonViewport } from "./ComparisonViewport"

type ViewMode = "split" | "swipe" | "onion"

async function readImage(url: string, signal: AbortSignal) {
  const response = await fetch(evidenceMediaUrl(url), {
    credentials: "include",
    signal,
  })
  if (!response.ok) throw new Error("Could not load screenshot evidence.")
  const blobUrl = URL.createObjectURL(await response.blob())
  try {
    const image = new Image()
    image.src = blobUrl
    await image.decode()
    return image
  } finally {
    URL.revokeObjectURL(blobUrl)
  }
}

function Screenshot({
  image,
  label,
}: {
  image: HTMLImageElement
  label: string
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    ref.current?.getContext("2d")?.drawImage(image, 0, 0)
  }, [image])
  return (
    <canvas
      ref={ref}
      width={image.naturalWidth}
      height={image.naturalHeight}
      className="block h-auto w-full"
      role="img"
      aria-label={label}
    />
  )
}

export function EvidenceViewer({ scenario }: { scenario: FrontendScenario }) {
  const container = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(
    () => typeof IntersectionObserver === "undefined"
  )
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: "200px" }
    )
    observer.observe(container.current!)
    return () => observer.disconnect()
  }, [])
  return (
    <div ref={container} className="min-w-0">
      {visible ? (
        <LoadedEvidence scenario={scenario} />
      ) : (
        <div
          className="flex min-h-[180px] items-center justify-center text-sm text-secondary"
          style={{
            aspectRatio: `${scenario.width ?? 900} / ${scenario.height ?? 560}`,
            maxHeight: "min(60vh, 560px)",
          }}
        >
          Loading screenshots…
        </div>
      )}
    </div>
  )
}

function LoadedEvidence({ scenario }: { scenario: FrontendScenario }) {
  const [mode, setMode] = useState<ViewMode>("split")
  const [swipe, setSwipe] = useState(50)
  const [opacity, setOpacity] = useState(50)
  const [state, setState] = useState<{
    source: string
    pair?: { before: HTMLImageElement; after: HTMLImageElement }
    error?: string
  }>()
  const beforeUrl = scenario.before_url
  const afterUrl = scenario.after_url
  const source = JSON.stringify([beforeUrl, afterUrl])
  const loaded = state?.source === source ? state.pair : undefined
  const error = state?.source === source ? state.error : undefined

  function moveDivider(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.parentElement!.getBoundingClientRect()
    if (bounds.width)
      setSwipe(
        Math.round(
          Math.max(
            0,
            Math.min(100, ((event.clientX - bounds.left) / bounds.width) * 100)
          )
        )
      )
  }

  useEffect(() => {
    if (!beforeUrl || !afterUrl) return
    const controller = new AbortController()
    void Promise.all([
      readImage(beforeUrl, controller.signal),
      readImage(afterUrl, controller.signal),
    ])
      .then(([before, after]) => {
        if (controller.signal.aborted) return
        if (
          before.naturalWidth !== after.naturalWidth ||
          before.naturalHeight !== after.naturalHeight
        )
          throw new Error(
            "Screenshots have different dimensions. Capture both at the same viewport."
          )
        if (before.naturalWidth * before.naturalHeight > 16_000_000)
          throw new Error(
            "Screenshots are too large for interactive inspection."
          )
        setState({ source, pair: { before, after } })
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setState({
            source,
            error:
              cause instanceof Error
                ? cause.message
                : "Could not load screenshots.",
          })
      })
    return () => controller.abort()
  }, [beforeUrl, afterUrl, source])

  if (error)
    return (
      <p role="alert" className="p-space-4 text-sm text-error-secondary">
        {error}
      </p>
    )
  if (!beforeUrl || !afterUrl)
    return (
      <div className="flex flex-col gap-space-3 p-space-4 text-sm text-secondary">
        <p>
          {scenario.error ??
            "No paired screenshots. This state has not been visually compared."}
        </p>
        {(beforeUrl || afterUrl) && (
          <figure>
            <figcaption className="mb-space-2 text-xs">
              {afterUrl ? "PR state only" : "Base state only"} · No pixel
              comparison available
            </figcaption>
            <img
              src={evidenceMediaUrl((afterUrl || beforeUrl)!)}
              alt={afterUrl ? "PR interaction state" : "Base interaction state"}
              className="h-auto max-w-full rounded-md border border-default"
              loading="lazy"
            />
          </figure>
        )}
      </div>
    )
  if (!loaded)
    return (
      <p role="status" className="p-space-4 text-sm text-secondary">
        Loading screenshots…
      </p>
    )

  const combined = (
    <div className="relative isolate">
      <Screenshot image={loaded.after} label="After screenshot" />
      <div
        className="pointer-events-none absolute inset-0"
        style={
          mode === "swipe"
            ? { clipPath: `inset(0 ${100 - swipe}% 0 0)` }
            : { opacity: 1 - opacity / 100 }
        }
      >
        <Screenshot image={loaded.before} label="Before screenshot" />
      </div>
      {mode === "swipe" && (
        <div
          role="slider"
          aria-label="Before after divider"
          aria-orientation="horizontal"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={swipe}
          aria-valuetext={`${swipe}% before, ${100 - swipe}% after`}
          tabIndex={0}
          className="absolute inset-y-0 w-6 -translate-x-1/2 cursor-col-resize touch-none outline-offset-2 before:absolute before:inset-y-0 before:left-1/2 before:-translate-x-1/2 before:border-l-2 before:border-focus hover:before:border-l-4 focus-visible:outline-2 focus-visible:outline-[var(--border-focus)]"
          style={{ left: `${swipe}%` }}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.preventDefault()
            event.stopPropagation()
            event.currentTarget.focus({ preventScroll: true })
            event.currentTarget.setPointerCapture(event.pointerId)
            moveDivider(event)
          }}
          onPointerMove={(event) => {
            if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
            event.stopPropagation()
            moveDivider(event)
          }}
          onPointerUp={(event) => {
            event.stopPropagation()
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId)
          }}
          onPointerCancel={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId)
          }}
          onKeyDown={(event) => {
            if (event.ctrlKey || event.metaKey || event.altKey) return
            const step = event.shiftKey ? 10 : 1
            let next: number
            if (event.key === "ArrowLeft" || event.key === "ArrowDown")
              next = swipe - step
            else if (event.key === "ArrowRight" || event.key === "ArrowUp")
              next = swipe + step
            else if (event.key === "Home") next = 0
            else if (event.key === "End") next = 100
            else return
            event.preventDefault()
            event.stopPropagation()
            setSwipe(Math.max(0, Math.min(100, next)))
          }}
        />
      )}
    </div>
  )

  return (
    <div role="region" aria-label="Visual comparison" className="min-w-0">
      <ComparisonViewport
        width={loaded.before.naturalWidth}
        height={loaded.before.naturalHeight}
        panes={
          mode === "split"
            ? [
                {
                  id: "before",
                  label: "Before",
                  content: (
                    <Screenshot
                      image={loaded.before}
                      label="Before screenshot"
                    />
                  ),
                },
                {
                  id: "after",
                  label: "After",
                  content: (
                    <Screenshot image={loaded.after} label="After screenshot" />
                  ),
                },
              ]
            : [
                {
                  id: "comparison",
                  label: mode === "swipe" ? "Before / After" : "Before + After",
                  content: combined,
                },
              ]
        }
      />
      <div className="flex flex-wrap items-center justify-between gap-space-3 px-space-3 py-space-2">
        <GroupedTabs
          size="sm"
          value={mode}
          onChange={setMode}
          options={[
            { value: "split", display: "2-up" },
            { value: "swipe", display: "Swipe" },
            { value: "onion", display: "Onion Skin" },
          ]}
        />
        {mode === "onion" && (
          <div className="flex w-64 max-w-full items-center gap-space-3 text-xs text-secondary">
            <span className="shrink-0">After opacity</span>
            <Slider
              aria-label="After opacity"
              className="flex-1"
              min={0}
              max={100}
              value={opacity}
              onChange={(next) => {
                const value = Array.isArray(next) ? (next[0] ?? 50) : next
                setOpacity(value)
              }}
            />
            <span className="w-8 text-right tabular-nums">{opacity}%</span>
          </div>
        )}
      </div>
      {(scenario.before_video_url || scenario.after_video_url) && (
        <Card className="m-space-3 grid gap-space-3 p-space-3 sm:grid-cols-2">
          {(
            [
              ["Before", scenario.before_video_url],
              ["After", scenario.after_video_url],
            ] as const
          ).map(
            ([label, url]) =>
              url && (
                <figure key={label}>
                  <figcaption className="mb-space-2 text-xs text-secondary">
                    {label} recording
                  </figcaption>
                  <video
                    controls
                    preload="metadata"
                    src={evidenceMediaUrl(url)}
                    className="w-full rounded-sm"
                  />
                </figure>
              )
          )}
        </Card>
      )}
    </div>
  )
}
