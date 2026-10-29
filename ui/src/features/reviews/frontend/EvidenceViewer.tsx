import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Slider } from "@langchain/macaw-components/Slider"
import { useEffect, useRef, useState } from "react"

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
          className="pointer-events-none absolute inset-y-0 border-l-2 border-focus"
          style={{ left: `${swipe}%` }}
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
        {mode !== "split" && (
          <div className="flex w-64 max-w-full items-center gap-space-3 text-xs text-secondary">
            <span className="shrink-0">
              {mode === "swipe" ? "Divider" : "After opacity"}
            </span>
            <Slider
              aria-label={
                mode === "swipe" ? "Before after divider" : "After opacity"
              }
              className="flex-1"
              min={0}
              max={100}
              value={mode === "swipe" ? swipe : opacity}
              onChange={(next) => {
                const value = Array.isArray(next) ? (next[0] ?? 50) : next
                if (mode === "swipe") setSwipe(value)
                else setOpacity(value)
              }}
            />
            <span className="w-8 text-right tabular-nums">
              {mode === "swipe" ? swipe : opacity}%
            </span>
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
