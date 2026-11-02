import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Slider } from "@langchain/macaw-components/Slider"
import type { FrontendAnnotation, FrontendScenario } from "@openswe/plugin-sdk"
import { useEffect, useRef, useState, type PointerEvent } from "react"

import { useImage, useNearViewport, type LoadedImage } from "../lib/media"
import {
  errorMessage,
  isForbidden,
  useMedia,
  type RunMedia,
} from "../lib/queries"
import { ComparisonViewport } from "./ComparisonViewport"
import { ShareAgainButton } from "./ShareRepositories"

type ViewMode = "split" | "swipe" | "onion"

/** Past this many pixels a pair is too heavy to inspect interactively. */
const MAX_PIXELS = 16_000_000

function Screenshot({
  image,
  label,
  annotation,
}: {
  image: LoadedImage
  label: string
  annotation?: FrontendAnnotation
}) {
  return (
    <div className="relative">
      <img
        src={image.url}
        alt={label}
        draggable={false}
        className="block h-auto w-full"
      />
      {annotation && (
        <div
          role="img"
          aria-label={`${label}: ${annotation.title}`}
          className="pointer-events-none absolute border-2 border-focus"
          style={{
            left: `${(annotation.x / image.width) * 100}%`,
            top: `${(annotation.y / image.height) * 100}%`,
            width: `${(annotation.width / image.width) * 100}%`,
            height: `${(annotation.height / image.height) * 100}%`,
          }}
        />
      )}
    </div>
  )
}

interface EvidenceViewerProps {
  media: RunMedia
  scenario: FrontendScenario
  annotation?: FrontendAnnotation
  onClearAnnotation?: () => void
}

/** A scenario's screenshots, read once the card comes near the screen. */
export function EvidenceViewer(props: EvidenceViewerProps) {
  const { scenario } = props
  const [container, near] = useNearViewport<HTMLDivElement>()
  return (
    <div ref={container} className="min-w-0">
      {near ? (
        <LoadedEvidence {...props} />
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

function pairError(before: LoadedImage, after: LoadedImage): string | null {
  if (before.width !== after.width || before.height !== after.height)
    return "Screenshots have different dimensions. Capture both at the same viewport."
  if (before.width * before.height > MAX_PIXELS)
    return "Screenshots are too large for interactive inspection."
  return null
}

function LoadedEvidence({
  media,
  scenario,
  annotation,
  onClearAnnotation,
}: EvidenceViewerProps) {
  const [mode, setMode] = useState<ViewMode>("split")
  const [swipe, setSwipe] = useState(50)
  const [opacity, setOpacity] = useState(50)
  const beforeFile = useMedia(media, scenario.before_url)
  const afterFile = useMedia(media, scenario.after_url)
  const before = useImage(beforeFile.data)
  const after = useImage(afterFile.data)
  const failed = beforeFile.error ?? afterFile.error

  function moveDivider(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.parentElement?.getBoundingClientRect()
    if (bounds?.width)
      setSwipe(
        Math.round(
          Math.max(
            0,
            Math.min(100, ((event.clientX - bounds.left) / bounds.width) * 100)
          )
        )
      )
  }

  if (failed)
    return (
      <div className="flex flex-col gap-space-2 p-space-4">
        <p role="alert" className="text-sm text-error-secondary">
          Could not load screenshots: {errorMessage(failed)}
        </p>
        {isForbidden(failed) && (
          <ShareAgainButton repository={media.repository} />
        )}
      </div>
    )
  if (!scenario.before_url || !scenario.after_url) {
    const single = scenario.after_url ? after : before
    return (
      <div className="flex flex-col gap-space-3 p-space-4 text-sm text-secondary">
        <p>
          {scenario.error ??
            "No paired screenshots. This state has not been visually compared."}
        </p>
        {(scenario.before_url || scenario.after_url) && (
          <figure>
            <figcaption className="mb-space-2 text-xs">
              {scenario.after_url ? "PR state only" : "Base state only"} · No
              pixel comparison available
            </figcaption>
            {single.image ? (
              <img
                src={single.image.url}
                alt={
                  scenario.after_url
                    ? "PR interaction state"
                    : "Base interaction state"
                }
                className="h-auto max-w-full rounded-md border border-default"
              />
            ) : (
              <p role="status" className="text-xs">
                {single.error ?? "Loading screenshot…"}
              </p>
            )}
          </figure>
        )}
      </div>
    )
  }
  const decodeError = before.error ?? after.error
  const mismatch =
    before.image && after.image ? pairError(before.image, after.image) : null
  if (decodeError || mismatch)
    return (
      <p role="alert" className="p-space-4 text-sm text-error-secondary">
        {decodeError ?? mismatch}
      </p>
    )
  if (!before.image || !after.image)
    return (
      <p role="status" className="p-space-4 text-sm text-secondary">
        Loading screenshots…
      </p>
    )

  const combined = (
    <div className="relative isolate">
      <Screenshot
        image={after.image}
        label="After screenshot"
        annotation={annotation}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={
          mode === "swipe"
            ? { clipPath: `inset(0 ${100 - swipe}% 0 0)` }
            : { opacity: 1 - opacity / 100 }
        }
      >
        <Screenshot
          image={before.image}
          label="Before screenshot"
          annotation={annotation}
        />
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
      {annotation && (
        <div className="flex flex-col gap-space-2 border-y border-default bg-surface-level-3 px-space-4 py-space-2 sm:flex-row sm:items-start sm:justify-between">
          <p role="status" className="text-xs text-secondary">
            <span className="font-medium text-primary">{annotation.title}</span>
            {" · "}
            {annotation.body}
          </p>
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            className="self-start"
            onClick={onClearAnnotation}
          >
            Clear highlight
          </Button>
        </div>
      )}
      <ComparisonViewport
        key={JSON.stringify(annotation)}
        width={before.image.width}
        height={before.image.height}
        panes={
          mode === "split"
            ? [
                {
                  id: "before",
                  label: "Before",
                  content: (
                    <Screenshot
                      image={before.image}
                      label="Before screenshot"
                      annotation={annotation}
                    />
                  ),
                },
                {
                  id: "after",
                  label: "After",
                  content: (
                    <Screenshot
                      image={after.image}
                      label="After screenshot"
                      annotation={annotation}
                    />
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
              onChange={(next) =>
                setOpacity(Array.isArray(next) ? (next[0] ?? 50) : next)
              }
            />
            <span className="w-8 text-right tabular-nums">{opacity}%</span>
          </div>
        )}
      </div>
      {(scenario.before_video_url || scenario.after_video_url) && (
        <Card className="m-space-3 grid gap-space-3 p-space-3 sm:grid-cols-2">
          {scenario.before_video_url && (
            <Recording
              media={media}
              label="Before"
              name={scenario.before_video_url}
            />
          )}
          {scenario.after_video_url && (
            <Recording
              media={media}
              label="After"
              name={scenario.after_video_url}
            />
          )}
        </Card>
      )}
    </div>
  )
}

function Recording({
  media,
  label,
  name,
}: {
  media: RunMedia
  label: string
  name: string
}) {
  const file = useMedia(media, name)
  const video = useRef<HTMLVideoElement>(null)
  const blob = file.data
  useEffect(() => {
    const element = video.current
    if (!blob || !element) return
    const url = URL.createObjectURL(blob)
    element.src = url
    return () => {
      element.removeAttribute("src")
      URL.revokeObjectURL(url)
    }
  }, [blob])
  return (
    <figure>
      <figcaption className="mb-space-2 text-xs text-secondary">
        {label} recording
      </figcaption>
      <video
        ref={video}
        controls
        preload="metadata"
        hidden={!blob}
        className="w-full rounded-sm"
      />
      {!blob && (
        <p role="status" className="text-xs text-secondary">
          {file.error
            ? `Could not load the recording: ${errorMessage(file.error)}`
            : "Loading recording…"}
        </p>
      )}
    </figure>
  )
}
