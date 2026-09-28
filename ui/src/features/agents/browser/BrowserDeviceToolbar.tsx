import { useState } from "react"
import { Link2, Unlink2, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import {
  BROWSER_DEVICE_TOOLBAR_HEIGHT,
  BROWSER_VIEWPORT_MAX_DIMENSION,
  BROWSER_VIEWPORT_MIN_DIMENSION,
  BROWSER_VIEWPORT_PRESETS,
  type BrowserViewport,
  type FixedBrowserViewport,
  isValidViewportSize,
  presetViewport,
  resizeFreeformViewport,
} from "@/features/agents/browser/browserViewport"
import { ScreenRotationIcon } from "@/features/agents/browser/ScreenRotationIcon"
import { cn } from "@/lib/utils"

const RESPONSIVE_VALUE = "responsive"
const SELECT_ITEMS = [
  { value: RESPONSIVE_VALUE, label: "Responsive" },
  ...BROWSER_VIEWPORT_PRESETS.map((preset) => ({
    value: preset.id,
    label: preset.label,
  })),
]

interface Props {
  readonly viewport: FixedBrowserViewport
  readonly width: number
  readonly aspectRatio: number | null
  readonly onAspectRatioChange: (aspectRatio: number | null) => void
  readonly onChange: (viewport: BrowserViewport) => Promise<void>
}

/** Device picker, size inputs, rotate, and aspect lock for a fixed viewport. */
export function BrowserDeviceToolbar({
  viewport,
  width,
  aspectRatio,
  onAspectRatioChange,
  onChange,
}: Props) {
  const [pending, setPending] = useState(false)
  const [customSize, setCustomSize] = useState<{
    readonly width: string
    readonly height: string
  } | null>(null)
  const presentedSize = customSize ?? {
    width: String(viewport.width),
    height: String(viewport.height),
  }
  const selectedValue =
    viewport.mode === "preset" ? viewport.presetId : RESPONSIVE_VALUE
  const customWidth = Number(presentedSize.width)
  const customHeight = Number(presentedSize.height)
  const customValid = isValidViewportSize({
    width: customWidth,
    height: customHeight,
  })

  const apply = (next: BrowserViewport, nextAspectRatio = aspectRatio) => {
    setPending(true)
    void onChange(next).then(
      () => {
        setPending(false)
        setCustomSize(null)
        onAspectRatioChange(nextAspectRatio)
      },
      () => setPending(false)
    )
  }

  const applyCustomSize = () => {
    if (
      !customValid ||
      (customWidth === viewport.width && customHeight === viewport.height)
    ) {
      setCustomSize(null)
      return
    }
    apply({ mode: "freeform", width: customWidth, height: customHeight })
  }

  const updateCustomDimension = (axis: "width" | "height", value: string) => {
    setCustomSize((current) => {
      const next = {
        width:
          axis === "width" ? value : (current?.width ?? String(viewport.width)),
        height:
          axis === "height"
            ? value
            : (current?.height ?? String(viewport.height)),
      }
      const numeric = Number(value)
      if (
        aspectRatio === null ||
        !Number.isInteger(numeric) ||
        numeric < BROWSER_VIEWPORT_MIN_DIMENSION ||
        numeric > BROWSER_VIEWPORT_MAX_DIMENSION
      ) {
        return next
      }
      const resized = resizeFreeformViewport(
        viewport,
        axis === "width"
          ? { x: numeric - viewport.width, y: 0 }
          : { x: 0, y: numeric - viewport.height },
        1,
        axis === "width" ? "east" : "south",
        aspectRatio
      )
      return { width: String(resized.width), height: String(resized.height) }
    })
  }

  const selectViewport = (value: string | null) => {
    if (!value) return
    if (value === RESPONSIVE_VALUE) {
      if (viewport.mode === "freeform") return
      apply({
        mode: "freeform",
        width: viewport.width,
        height: viewport.height,
      })
      return
    }
    const preset = BROWSER_VIEWPORT_PRESETS.find(
      (candidate) => candidate.id === value
    )
    if (!preset) return
    apply(
      presetViewport(preset.id),
      aspectRatio === null ? null : preset.width / preset.height
    )
  }

  const rotate = () => {
    const hasCustomSize =
      customValid &&
      (customWidth !== viewport.width || customHeight !== viewport.height)
    const source: FixedBrowserViewport = hasCustomSize
      ? { mode: "freeform", width: customWidth, height: customHeight }
      : viewport
    apply(
      { ...source, width: source.height, height: source.width },
      aspectRatio === null ? null : 1 / aspectRatio
    )
  }

  const compact = width < 440
  const inputClass = cn(
    "h-6 px-1.5 font-mono text-xs tabular-nums",
    width >= 360 ? "w-14" : "w-13"
  )

  return (
    <div
      className="sticky top-0 left-0 z-50 flex [scrollbar-width:none] items-center gap-0.5 overflow-x-auto border-b border-border/70 bg-background/95 px-1.5 shadow-xs backdrop-blur-md [&::-webkit-scrollbar]:hidden"
      style={{ width, height: BROWSER_DEVICE_TOOLBAR_HEIGHT }}
      role="toolbar"
      aria-label="Browser device toolbar"
      data-browser-device-toolbar
      onBlur={(event) => {
        const nextTarget = event.relatedTarget
        if (
          nextTarget instanceof Node &&
          event.currentTarget.contains(nextTarget)
        )
          return
        if (
          nextTarget instanceof HTMLElement &&
          nextTarget.closest('[data-slot="select-content"]')
        ) {
          return
        }
        applyCustomSize()
      }}
    >
      {width >= 560 ? (
        <span className="mr-0.5 shrink-0 text-[0.625rem] font-medium text-muted-foreground">
          Dimensions
        </span>
      ) : null}
      <Select
        modal={false}
        value={selectedValue}
        onValueChange={selectViewport}
        items={SELECT_ITEMS}
        disabled={pending}
      >
        <SelectTrigger
          size="sm"
          className={cn(
            "shrink-0 justify-between border-transparent bg-transparent",
            compact ? "w-24" : "w-36"
          )}
          aria-label="Browser device preset"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="start" alignItemWithTrigger={false}>
          <SelectItem value={RESPONSIVE_VALUE}>Responsive</SelectItem>
          <SelectGroup>
            <SelectLabel>Devices</SelectLabel>
            {BROWSER_VIEWPORT_PRESETS.map((preset) => (
              <SelectItem key={preset.id} value={preset.id}>
                <span className="flex w-full items-center justify-between gap-5">
                  <span>{preset.label}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {preset.width} × {preset.height}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <form
        className="m-0 flex min-w-0 shrink-0 items-center gap-0.5 border-0 p-0"
        aria-label="Viewport dimensions"
        onSubmit={(event) => {
          event.preventDefault()
          applyCustomSize()
        }}
      >
        <Input
          type="number"
          inputMode="numeric"
          min={BROWSER_VIEWPORT_MIN_DIMENSION}
          max={BROWSER_VIEWPORT_MAX_DIMENSION}
          value={presentedSize.width}
          disabled={pending}
          onFocus={() =>
            setCustomSize(
              (current) =>
                current ?? {
                  width: String(viewport.width),
                  height: String(viewport.height),
                }
            )
          }
          onChange={(event) =>
            updateCustomDimension("width", event.target.value)
          }
          aria-label="Viewport width"
          aria-invalid={!customValid}
          className={inputClass}
        />
        <span className="text-xs text-muted-foreground">×</span>
        <Input
          type="number"
          inputMode="numeric"
          min={BROWSER_VIEWPORT_MIN_DIMENSION}
          max={BROWSER_VIEWPORT_MAX_DIMENSION}
          value={presentedSize.height}
          disabled={pending}
          onFocus={() =>
            setCustomSize(
              (current) =>
                current ?? {
                  width: String(viewport.width),
                  height: String(viewport.height),
                }
            )
          }
          onChange={(event) =>
            updateCustomDimension("height", event.target.value)
          }
          aria-label="Viewport height"
          aria-invalid={!customValid}
          className={inputClass}
        />
      </form>

      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant={aspectRatio === null ? "ghost" : "secondary"}
              size="icon-sm"
              type="button"
              aria-label={
                aspectRatio === null
                  ? "Lock viewport aspect ratio"
                  : "Unlock viewport aspect ratio"
              }
              aria-pressed={aspectRatio !== null}
              disabled={pending || !customValid}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() =>
                onAspectRatioChange(
                  aspectRatio === null ? customWidth / customHeight : null
                )
              }
            />
          }
        >
          {aspectRatio === null ? <Unlink2 /> : <Link2 />}
        </TooltipTrigger>
        <TooltipPopup side="top">
          {aspectRatio === null ? "Lock aspect ratio" : "Unlock aspect ratio"}
        </TooltipPopup>
      </Tooltip>
      <Button
        variant="ghost"
        size="icon-sm"
        type="button"
        aria-label="Rotate viewport"
        disabled={pending}
        onClick={rotate}
      >
        <ScreenRotationIcon />
      </Button>
      <span className="sticky right-0 ml-auto flex bg-background/95">
        <Button
          variant="ghost"
          size="icon-sm"
          type="button"
          aria-label="Close device toolbar"
          disabled={pending}
          onClick={() => apply({ mode: "fill" }, null)}
        >
          <X />
        </Button>
      </span>
    </div>
  )
}
