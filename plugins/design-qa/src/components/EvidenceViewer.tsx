import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Text } from "@langchain/macaw-components/Text"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Slider } from "@langchain/macaw-components/Slider"
import { cn } from "@langchain/macaw-components/utils/cn"
import { useEffect, useRef, useState, type PointerEvent } from "react"

import {
  StoredCapture,
  type FrontendAnnotation,
  type FrontendScenario,
  type ScenarioShots,
} from "../lib/capture"
import { useImage, useNearViewport, type LoadedImage } from "../lib/media"
import {
  errorMessage,
  isForbidden,
  useMedia,
  type RunMedia,
} from "../lib/queries"
import { ComparisonViewport } from "./ComparisonViewport"
import { ShareAgainButton } from "./ShareRepositories"
import { useUsage } from "../lib/usage"

type ViewMode = "split" | "swipe" | "onion"

/** Past this many pixels a pair is too heavy to inspect interactively. */
const MAX_PIXELS = 16_000_000

function Screenshot({
  image,
  label,
  annotations = [],
}: {
  image: LoadedImage
  label: string
  annotations?: FrontendAnnotation[]
}) {
  const [original, setOriginal] = useState(false)
  const opener = useRef<HTMLButtonElement>(null)
  return (
    <div className="relative">
      <Button
        ref={opener}
        data-screenshot
        size="sm"
        color="secondary"
        variant="plain"
        aria-label={`Open ${label.toLowerCase()} at natural size`}
        className="block h-auto w-full cursor-zoom-in rounded-none border-0 p-0"
        onClick={() => setOriginal(true)}
      >
        <img
          src={image.url}
          alt={label}
          draggable={false}
          className="block h-auto w-full"
        />
      </Button>
      {annotations.map((annotation, index) => (
        <div
          key={index}
          role="img"
          aria-label={`${label}: ${annotation.title}`}
          className={cn(
            "pointer-events-none absolute border-2",
            annotation.severity === "error" ? "border-error" : "border-warning"
          )}
          style={{
            left: `${(annotation.x / image.width) * 100}%`,
            top: `${(annotation.y / image.height) * 100}%`,
            width: `${(annotation.width / image.width) * 100}%`,
            height: `${(annotation.height / image.height) * 100}%`,
          }}
        />
      ))}
      <Dialog open={original} onOpenChange={setOriginal}>
        <DialogContent
          title={`${label} · ${image.width} × ${image.height}`}
          description="Natural size, without annotations. Scroll to inspect the full image."
          className="max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)]"
          childrenClassName="min-h-0 overflow-auto p-space-3"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            opener.current?.focus({ preventScroll: true })
          }}
        >
          <img
            src={image.url}
            alt={`${label} at natural size`}
            width={image.width}
            height={image.height}
            className="block max-w-none"
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}

interface EvidenceViewerProps {
  media: RunMedia
  scenario: FrontendScenario
  /** The pair to compare: the scenario's light or dark screenshots. */
  shots: ScenarioShots
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
  shots,
  annotation,
  onClearAnnotation,
}: EvidenceViewerProps) {
  const [mode, setMode] = useState<ViewMode>("split")
  const [showHints, setShowHints] = useState(true)
  const [hintSelection, setHintSelection] = useState({
    annotation,
    hidden: false,
  })
  if (hintSelection.annotation !== annotation)
    setHintSelection({ annotation, hidden: false })
  const selectedAnnotation = hintSelection.hidden ? undefined : annotation
  const hintsVisible = showHints || Boolean(selectedAnnotation)
  const annotations = selectedAnnotation
    ? [selectedAnnotation]
    : showHints
      ? scenario.annotations
      : []
  const record = useUsage()
  const [swipe, setSwipe] = useState(50)
  const [opacity, setOpacity] = useState(50)
  const beforeFile = useMedia(media, shots.before)
  const afterFile = useMedia(media, shots.after)
  const before = useImage(beforeFile.data)
  const after = useImage(afterFile.data)
  const failed = beforeFile.error ?? afterFile.error
  const other = StoredCapture.shots(
    scenario,
    shots.theme === "dark" ? "light" : "dark"
  )
  const shown = Boolean(before.image && after.image)
  useMedia(media, other.before, shown)
  useMedia(media, other.after, shown)

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
  if (!shots.before || !shots.after) {
    const single = shots.after ? after : before
    return (
      <div className="flex flex-col gap-space-3 p-space-4 text-sm text-secondary">
        <p>
          {scenario.error ??
            "No paired screenshots. This state has not been visually compared."}
        </p>
        {(shots.before || shots.after) && (
          <figure>
            <figcaption className="mb-space-2 text-xs">
              {shots.after ? "PR state only" : "Base state only"}. No pixel
              comparison available.
            </figcaption>
            {single.image ? (
              <Screenshot
                image={single.image}
                label={
                  shots.after
                    ? "PR interaction state"
                    : "Base interaction state"
                }
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
        annotations={annotations}
      />
      <div
        inert
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
          annotations={annotations}
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
          aria-description="Use arrow keys to move the divider, Shift for larger steps, and Home or End to reveal one screenshot."
          className="group absolute inset-y-0 w-6 -translate-x-1/2 cursor-col-resize touch-none outline-none before:absolute before:inset-y-0 before:left-1/2 before:-translate-x-1/2 before:border-l-2 before:border-brand hover:before:border-l-4"
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
        >
          <span
            aria-hidden="true"
            className="absolute top-1/2 left-1/2 flex h-8 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-sm border border-brand bg-surface-level-2 text-primary group-focus-visible:outline-2 group-focus-visible:outline-offset-2 group-focus-visible:outline-[var(--border-focus)]"
          >
            ↔
          </span>
        </div>
      )}
    </div>
  )

  return (
    <div role="region" aria-label="Visual comparison" className="min-w-0">
      {(scenario.annotations.length > 0 || annotation) && (
        <div className="flex flex-wrap items-start justify-between gap-space-2 border-t border-default bg-surface-level-3 px-space-3 py-space-2">
          <div className="flex min-w-0 flex-1 flex-col gap-space-1">
            <Text variant="xs" className="text-secondary" role="status">
              {hintsVisible
                ? `${annotations.length} highlighted ${annotations.length === 1 ? "area" : "areas"} · review hints`
                : "Hints hidden · original appearance"}
            </Text>
            {hintsVisible &&
              annotations.map((note, index) => (
                <Text key={index} variant="xs" className="text-secondary">
                  <span className="font-medium text-primary">
                    {note.title}:
                  </span>{" "}
                  {note.body}
                </Text>
              ))}
          </div>
          <Button
            size="sm"
            color="secondary"
            variant="outlined"
            aria-pressed={hintsVisible}
            onClick={() => {
              setShowHints(!hintsVisible)
              setHintSelection({ annotation, hidden: hintsVisible })
              if (hintsVisible) onClearAnnotation?.()
            }}
          >
            {hintsVisible ? "Hide hints" : "Show hints"}
          </Button>
        </div>
      )}
      <ComparisonViewport
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
                      annotations={annotations}
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
                      annotations={annotations}
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
          onChange={(next) => {
            record(`compare_${next}`)
            setMode(next)
          }}
          options={[
            { value: "split", display: "2-up" },
            { value: "swipe", display: "Swipe" },
            { value: "onion", display: "Onion Skin" },
          ]}
        />
        {mode === "swipe" && (
          <Text variant="xs" className="text-secondary">
            Before ← divider → After · Drag or focus the grip and use arrow keys
          </Text>
        )}
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
