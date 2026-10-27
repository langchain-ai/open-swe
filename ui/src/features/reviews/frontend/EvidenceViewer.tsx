import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Slider } from "@langchain/macaw-components/Slider"
import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"
import { evidenceMediaUrl, type FrontendScenario } from "./api"
import { ComparisonViewport } from "./ComparisonViewport"
import {
  comparePixels,
  cropRegion,
  type PixelComparison,
  type PixelRect,
} from "./pixels"

type ViewMode = "overlay" | "split" | "wipe" | "diff" | "toggle"
interface LoadedPair {
  before: HTMLImageElement
  after: HTMLImageElement
  beforeData: Uint8ClampedArray
  afterData: Uint8ClampedArray
  width: number
  height: number
}

async function readImage(
  url: string,
  signal: AbortSignal
): Promise<HTMLImageElement> {
  const response = await fetch(evidenceMediaUrl(url), {
    credentials: "include",
    signal,
  })
  if (!response.ok)
    throw new Error("Could not load protected screenshot evidence.")
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

function pixels(image: HTMLImageElement): Uint8ClampedArray {
  const canvas = document.createElement("canvas")
  canvas.width = image.naturalWidth
  canvas.height = image.naturalHeight
  const context = canvas.getContext("2d", { willReadFrequently: true })
  if (!context) throw new Error("Pixel inspection needs a browser canvas.")
  context.drawImage(image, 0, 0)
  return context.getImageData(0, 0, canvas.width, canvas.height).data
}

function PixelCanvas({
  data,
  width,
  height,
  className,
  style,
  label,
}: {
  data: Uint8ClampedArray
  width: number
  height: number
  className?: string
  style?: React.CSSProperties
  label: string
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const context = ref.current?.getContext("2d")
    if (!context) return
    const image = context.createImageData(width, height)
    image.data.set(data)
    context.putImageData(image, 0, 0)
  }, [data, width, height])
  return (
    <canvas
      ref={ref}
      width={width}
      height={height}
      className={className}
      style={style}
      aria-label={label}
      role="img"
    />
  )
}

function ScreenshotCanvas({
  image,
  crop,
  label,
}: {
  image: HTMLImageElement
  crop?: PixelRect
  label: string
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const area = crop ?? {
    x: 0,
    y: 0,
    width: image.naturalWidth,
    height: image.naturalHeight,
  }
  useEffect(() => {
    const context = ref.current?.getContext("2d")
    if (context)
      context.drawImage(
        image,
        area.x,
        area.y,
        area.width,
        area.height,
        0,
        0,
        area.width,
        area.height
      )
  }, [image, area.x, area.y, area.width, area.height])
  return (
    <canvas
      ref={ref}
      width={area.width}
      height={area.height}
      className="block h-auto w-full"
      aria-label={label}
      role="img"
    />
  )
}

function DiffCrop({
  comparison,
  crop,
}: {
  comparison: PixelComparison
  crop: PixelRect
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const source = document.createElement("canvas")
    source.width = comparison.width
    source.height = comparison.height
    const sourceContext = source.getContext("2d")
    const context = ref.current?.getContext("2d")
    if (!sourceContext || !context) return
    const data = sourceContext.createImageData(
      comparison.width,
      comparison.height
    )
    data.data.set(comparison.diff)
    sourceContext.putImageData(data, 0, 0)
    context.drawImage(
      source,
      crop.x,
      crop.y,
      crop.width,
      crop.height,
      0,
      0,
      crop.width,
      crop.height
    )
  }, [comparison, crop.x, crop.y, crop.width, crop.height])
  return (
    <canvas
      ref={ref}
      width={crop.width}
      height={crop.height}
      className="block h-auto w-full"
      aria-label="Selected region pixel differences"
      role="img"
    />
  )
}

export function EvidenceViewer({ scenario }: { scenario: FrontendScenario }) {
  const [mode, setMode] = useState<ViewMode>("overlay")
  const [opacity, setOpacity] = useState(72)
  const [threshold, setThreshold] = useState(scenario.metrics?.threshold ?? 18)
  const [wipe, setWipe] = useState(50)
  const [showPixels, setShowPixels] = useState(true)
  const [showRegions, setShowRegions] = useState(false)
  const [showAnnotations, setShowAnnotations] = useState(true)
  const [regionIndex, setRegionIndex] = useState(0)
  const [focus, setFocus] = useState<PixelRect | null>(null)
  const [singleImage, setSingleImage] = useState<"before" | "after">("after")
  const [localMasks, setLocalMasks] = useState<PixelRect[]>([])
  const [drawingMask, setDrawingMask] = useState(false)
  const [includeIgnored, setIncludeIgnored] = useState(false)
  const [state, setState] = useState<{
    source: string
    loaded?: LoadedPair
    error?: string
  } | null>(null)

  const beforeUrl = scenario.before_url
  const afterUrl = scenario.after_url
  const source = JSON.stringify([beforeUrl, afterUrl])
  const loaded = state?.source === source ? state.loaded : null
  const error = state?.source === source ? state.error : null
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
            "Screenshots have different dimensions. Capture both at the same viewport before comparing pixels."
          )
        if (before.naturalWidth * before.naturalHeight > 16_000_000)
          throw new Error(
            "Screenshots are too large for interactive inspection."
          )
        setState({
          source,
          loaded: {
            before,
            after,
            beforeData: pixels(before),
            afterData: pixels(after),
            width: before.naturalWidth,
            height: before.naturalHeight,
          },
        })
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setState({
            source,
            error:
              cause instanceof Error
                ? cause.message
                : "Could not inspect screenshots.",
          })
      })
    return () => controller.abort()
  }, [beforeUrl, afterUrl, source])

  const masks = [...scenario.mask_rects, ...localMasks]
  const comparison = loaded
    ? comparePixels(
        loaded.beforeData,
        loaded.afterData,
        loaded.width,
        loaded.height,
        {
          threshold,
          maskRects: includeIgnored ? [] : masks,
          tileSize: scenario.tile_size,
          minRegionPixels: scenario.min_region_pixels,
        }
      )
    : null
  const regions = comparison?.regions ?? []
  const selectedIndex = Math.min(regionIndex, Math.max(0, regions.length - 1))
  const selected = regions[selectedIndex]
  const crop =
    selected && loaded
      ? cropRegion(selected, loaded.width, loaded.height)
      : null
  const ratio =
    comparison && comparison.totalPixels
      ? comparison.changedPixels / comparison.totalPixels
      : 0
  const rectStyle = (rect: PixelRect) => ({
    left: `${(rect.x / (loaded?.width ?? 1)) * 100}%`,
    top: `${(rect.y / (loaded?.height ?? 1)) * 100}%`,
    width: `${(rect.width / (loaded?.width ?? 1)) * 100}%`,
    height: `${(rect.height / (loaded?.height ?? 1)) * 100}%`,
  })
  const updateLocalMasks = (next: PixelRect[]) => {
    setLocalMasks(next)
    setRegionIndex(0)
    setFocus(null)
    setIncludeIgnored(false)
  }
  const addMask = (rect: PixelRect) => {
    if (localMasks.length >= 20) return
    updateLocalMasks([...localMasks, rect])
    setDrawingMask(false)
  }
  const selectRegion = (index: number) => {
    const region = regions[index]
    if (!region) return
    setRegionIndex(index)
    setFocus({ ...region })
  }

  if (error)
    return (
      <Card role="alert" className="p-space-4 text-sm text-error-secondary">
        {error}
      </Card>
    )
  if (!beforeUrl || !afterUrl)
    return (
      <Card className="p-space-5 text-sm text-secondary">
        {scenario.error ??
          "This scenario has no paired screenshots. It does not count as visually reviewed."}
      </Card>
    )
  if (!loaded || !comparison)
    return (
      <p role="status" className="p-space-5 text-sm text-secondary">
        Loading paired screenshots…
      </p>
    )

  const exclusions = masks.map((rect, index) => (
    <div
      key={`${index}:${rect.x}:${rect.y}`}
      className={cn(
        "border-info pointer-events-none absolute overflow-hidden border-2 border-dashed",
        !includeIgnored && "bg-surface-level-1/50"
      )}
      style={rectStyle(rect)}
      title={
        index < scenario.mask_rects.length
          ? "Recorded ignore region"
          : "Local ignore region"
      }
    >
      <span className="bg-surface-level-1 px-space-1 text-xxs text-primary">
        {index < scenario.mask_rects.length
          ? "Recorded ignore"
          : `Local ignore ${index - scenario.mask_rects.length + 1}`}
      </span>
    </div>
  ))
  const overlays = (
    <>
      {showPixels && mode !== "diff" && mode !== "toggle" && (
        <PixelCanvas
          data={comparison.mask}
          width={loaded.width}
          height={loaded.height}
          label="Exact changed pixel overlay"
          className="pointer-events-none absolute inset-0 h-full w-full"
          style={{ opacity: opacity / 100 }}
        />
      )}
      {showRegions &&
        regions.map((region, index) => (
          <button
            key={`${region.x}:${region.y}`}
            type="button"
            aria-label={`Inspect changed region ${index + 1}`}
            aria-pressed={index === selectedIndex}
            onClick={() => selectRegion(index)}
            className={cn(
              "absolute rounded-sm border-2 border-warning bg-transparent",
              index === selectedIndex &&
                "outline-warning outline-2 outline-offset-2"
            )}
            style={rectStyle(region)}
          />
        ))}
      {showAnnotations &&
        scenario.annotations.map((annotation, index) => (
          <div
            key={`${annotation.title}:${index}`}
            title={`${annotation.title}: ${annotation.body}`}
            className="border-info pointer-events-none absolute rounded-sm border-2"
            style={rectStyle(annotation)}
          >
            <span className="absolute top-0 left-0 max-w-full truncate rounded-br-sm bg-surface-level-1 px-space-1 text-xxs text-primary">
              {annotation.title}
            </span>
          </div>
        ))}
      {exclusions}
    </>
  )

  const currentImage = singleImage === "before" ? loaded.before : loaded.after
  const imageContent = (
    <div className="relative isolate">
      {mode === "diff" ? (
        <PixelCanvas
          data={comparison.diff}
          width={loaded.width}
          height={loaded.height}
          label="Pixel difference image"
          className="block h-auto w-full"
        />
      ) : (
        <ScreenshotCanvas
          image={mode === "toggle" ? currentImage : loaded.after}
          label={
            mode === "toggle" && singleImage === "before"
              ? "Before screenshot"
              : "After screenshot"
          }
        />
      )}
      {mode === "wipe" && (
        <div
          className="pointer-events-none absolute inset-0"
          style={{ clipPath: `inset(0 ${100 - wipe}% 0 0)` }}
        >
          <ScreenshotCanvas image={loaded.before} label="Before screenshot" />
        </div>
      )}
      {mode === "toggle" ? exclusions : overlays}
      {mode === "wipe" && (
        <div
          className="pointer-events-none absolute inset-y-0 w-px bg-surface-level-1 shadow-md"
          style={{ left: `${wipe}%` }}
        />
      )}
    </div>
  )

  return (
    <div
      className="flex min-w-0 flex-col gap-space-4 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-focus"
      role="region"
      aria-label="Visual comparison"
      tabIndex={0}
      onKeyDown={(event) => {
        if (
          event.altKey ||
          event.ctrlKey ||
          event.metaKey ||
          event.repeat ||
          (event.target instanceof HTMLElement &&
            event.target.closest(
              "input,textarea,select,[contenteditable=true],[role=slider]"
            ))
        )
          return
        const key = event.key.toLowerCase()
        if (key === "h") {
          event.preventDefault()
          setMode("toggle")
          setSingleImage((image) => (image === "before" ? "after" : "before"))
        } else if (key === "d") {
          event.preventDefault()
          setShowPixels((shown) => !shown)
        } else if ((key === "j" || key === "k") && regions.length > 0) {
          event.preventDefault()
          selectRegion(
            (selectedIndex + regions.length + (key === "j" ? 1 : -1)) %
              regions.length
          )
        }
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <GroupedTabs
          size="sm"
          value={mode}
          onChange={setMode}
          options={[
            { value: "overlay", display: "Overlay" },
            { value: "split", display: "Side by side" },
            { value: "wipe", display: "Wipe" },
            { value: "diff", display: "Pixel diff" },
            { value: "toggle", display: "Blink" },
          ]}
        />
        <div className="flex items-center gap-space-2 text-xs text-secondary">
          <Badge color="secondary" size="sm">
            {comparison.totalPixels === 0
              ? "All pixels ignored"
              : `${(ratio * 100).toFixed(2)}% changed`}
          </Badge>
          <span>
            {comparison.changedPixels.toLocaleString()} pixels ·{" "}
            {regions.length} regions
          </span>
        </div>
      </div>
      <Card className="flex flex-wrap items-center gap-x-space-6 gap-y-space-3 p-space-3">
        <Checkbox
          label="Pixel highlights"
          checked={showPixels}
          onCheckedChange={(checked) => setShowPixels(checked === true)}
        />
        <Checkbox
          label="Region outlines"
          checked={showRegions}
          onCheckedChange={(checked) => setShowRegions(checked === true)}
        />
        <Checkbox
          label="Review annotations"
          checked={showAnnotations}
          onCheckedChange={(checked) => setShowAnnotations(checked === true)}
        />
        <label className="flex w-36 flex-col gap-space-2 text-xs text-secondary">
          Highlight opacity · {opacity}%
          <Slider
            aria-label="Highlight opacity"
            min={0}
            max={100}
            value={opacity}
            onChange={(value) =>
              setOpacity(Array.isArray(value) ? (value[0] ?? 72) : value)
            }
          />
        </label>
        <label className="flex w-36 flex-col gap-space-2 text-xs text-secondary">
          Difference threshold · {threshold}
          <Slider
            aria-label="Difference threshold"
            min={0}
            max={255}
            value={threshold}
            onChange={(value) => {
              setThreshold(Array.isArray(value) ? (value[0] ?? 18) : value)
              setRegionIndex(0)
              setFocus(null)
            }}
          />
        </label>
      </Card>
      <div className="flex flex-wrap items-center gap-space-2">
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={regions.length < 2}
          onClick={() =>
            selectRegion((selectedIndex + regions.length - 1) % regions.length)
          }
        >
          Previous change
        </Button>
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={regions.length < 2}
          onClick={() => selectRegion((selectedIndex + 1) % regions.length)}
        >
          Next change
        </Button>
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={!selected}
          onClick={() => selectRegion(selectedIndex)}
        >
          {selected ? `Focus change ${selectedIndex + 1}` : "Focus change"}
        </Button>
        <span className="text-xs text-secondary" aria-live="polite">
          {regions.length > 0
            ? `${selectedIndex + 1} of ${regions.length} changes`
            : "No change regions"}
        </span>
      </div>
      <Card className="p-0">
        <details
          onToggle={(event) => {
            if (!event.currentTarget.open) setDrawingMask(false)
          }}
        >
          <summary className="cursor-pointer px-space-3 py-space-2 text-xs font-medium text-secondary">
            Ignore noisy areas
            {masks.length > 0
              ? ` · ${masks.length} ${masks.length === 1 ? "region" : "regions"}`
              : ""}
          </summary>
          <div className="flex flex-col gap-space-3 border-t border-default p-space-3">
            <div className="flex flex-wrap items-center gap-space-2">
              <Button
                size="sm"
                color="secondary"
                variant={drawingMask ? "normal" : "outlined"}
                aria-pressed={drawingMask}
                disabled={localMasks.length >= 20}
                onClick={() => setDrawingMask(!drawingMask)}
              >
                {drawingMask ? "Cancel drawing" : "Draw ignore region"}
              </Button>
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                disabled={!selected || localMasks.length >= 20}
                onClick={() => selected && addMask(selected)}
              >
                Ignore selected change
              </Button>
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                disabled={localMasks.length === 0}
                onClick={() => updateLocalMasks(localMasks.slice(0, -1))}
              >
                Undo ignore
              </Button>
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                disabled={localMasks.length === 0}
                onClick={() => updateLocalMasks([])}
              >
                Clear local ignores
              </Button>
            </div>
            <p role="status" className="text-xs text-secondary">
              {drawingMask
                ? "Drag a rectangle on either screenshot to exclude noisy content from this view."
                : "Ignore regions adjust this view only. Saved screenshots, recorded metrics, and AI findings stay unchanged."}
            </p>
            {masks.length > 0 && (
              <>
                <Checkbox
                  label="Include ignored pixels"
                  checked={includeIgnored}
                  onCheckedChange={(checked) => {
                    setIncludeIgnored(checked === true)
                    setRegionIndex(0)
                    setFocus(null)
                  }}
                />
                <div className="flex flex-wrap items-center gap-space-2 text-xs text-secondary">
                  <span>
                    {scenario.mask_rects.length} recorded · {localMasks.length}{" "}
                    local ignore regions
                  </span>
                  {localMasks.map((_, index) => (
                    <Button
                      key={index}
                      size="xs"
                      color="secondary"
                      variant="outlined"
                      onClick={() =>
                        updateLocalMasks(
                          localMasks.filter((_rect, item) => item !== index)
                        )
                      }
                    >
                      {`Remove local ignore ${index + 1}`}
                    </Button>
                  ))}
                </div>
              </>
            )}
          </div>
        </details>
      </Card>
      {mode === "toggle" && (
        <div className="flex flex-wrap items-center gap-space-3">
          <GroupedTabs
            size="sm"
            value={singleImage}
            onChange={setSingleImage}
            options={[
              { value: "before", display: "Before" },
              { value: "after", display: "After" },
            ]}
          />
          <span className="text-xs text-secondary">
            Press H to blink between the original screenshots.
          </span>
        </div>
      )}
      <ComparisonViewport
        width={loaded.width}
        height={loaded.height}
        focus={focus}
        interaction={drawingMask ? "select" : "pan"}
        onSelect={addMask}
        panes={
          mode === "split"
            ? [
                {
                  id: "before",
                  label: "Before",
                  content: (
                    <div className="relative">
                      <ScreenshotCanvas
                        image={loaded.before}
                        label="Before screenshot"
                      />
                      {exclusions}
                    </div>
                  ),
                },
                { id: "after", label: "After", content: imageContent },
              ]
            : [
                {
                  id: "comparison",
                  label:
                    mode === "diff"
                      ? "Changed pixels"
                      : mode === "wipe"
                        ? "Before ← → After"
                        : mode === "toggle"
                          ? singleImage === "before"
                            ? "Before"
                            : "After"
                          : "After with changed pixels",
                  content: imageContent,
                },
              ]
        }
      />
      {mode === "wipe" && (
        <label className="mx-auto flex w-full max-w-sm flex-col gap-space-2 text-xs text-secondary">
          Before / after divider
          <Slider
            aria-label="Before after divider"
            min={0}
            max={100}
            value={wipe}
            onChange={(value) =>
              setWipe(Array.isArray(value) ? (value[0] ?? 50) : value)
            }
          />
        </label>
      )}
      <p className="text-xs text-secondary">
        Pink marks the actual changed pixels. Outlines group nearby changes.
        Pixel differences measure change; they do not determine correctness.
        These totals reflect the current threshold and ignore settings. Focus
        the comparison for shortcuts: H blink, D highlights, J/K next/previous
        change.
      </p>
      <Card className="min-w-0 overflow-hidden p-0">
        <div className="flex items-center justify-between gap-space-3 border-b border-default p-space-3">
          <div>
            <h3 className="text-sm font-medium">Inspect changed regions</h3>
            <p className="mt-space-1 text-xs text-secondary">
              {selected
                ? `Region ${selectedIndex + 1} of ${regions.length} · ${selected.changedPixels.toLocaleString()} changed pixels`
                : "No regions at this threshold."}
            </p>
          </div>
        </div>
        {crop && (
          <div className="grid grid-cols-3 divide-x divide-default">
            <div>
              <ImageLabel>Before crop</ImageLabel>
              <ScreenshotCanvas
                image={loaded.before}
                crop={crop}
                label="Selected region before"
              />
            </div>
            <div>
              <ImageLabel>After crop</ImageLabel>
              <ScreenshotCanvas
                image={loaded.after}
                crop={crop}
                label="Selected region after"
              />
            </div>
            <div>
              <ImageLabel>Pixel diff crop</ImageLabel>
              <DiffCrop comparison={comparison} crop={crop} />
            </div>
          </div>
        )}
      </Card>
      {(scenario.before_video_url || scenario.after_video_url) && (
        <Card className="p-space-3">
          <h3 className="mb-space-3 text-sm font-medium">
            Interaction recordings
          </h3>
          <div className="grid gap-space-3 sm:grid-cols-2">
            {(
              [
                ["Before", scenario.before_video_url],
                ["After", scenario.after_video_url],
              ] as const
            ).map(([label, url]) => (
              <div key={label}>
                <ImageLabel>{label}</ImageLabel>
                {url ? (
                  <video
                    controls
                    preload="metadata"
                    src={evidenceMediaUrl(url)}
                    className="w-full rounded-sm"
                    aria-label={`${label} interaction recording`}
                  />
                ) : (
                  <p className="p-space-3 text-xs text-secondary">
                    No recording captured.
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
      {scenario.annotations.length > 0 && (
        <Card className="p-space-4">
          <h3 className="mb-space-3 text-sm font-medium">Review notes</h3>
          <div className="space-y-space-3">
            {scenario.annotations.map((note, index) => (
              <div key={index}>
                <p className="text-sm font-medium">{note.title}</p>
                <p className="mt-space-1 text-sm text-secondary">{note.body}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function ImageLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-b border-default bg-surface-level-2 px-space-3 py-space-2 text-xs font-medium text-secondary">
      {children}
    </div>
  )
}
