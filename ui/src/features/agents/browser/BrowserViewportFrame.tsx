import { useCallback, useState } from "react"
import type { ReactNode } from "react"

import { BrowserDeviceToolbar } from "@/features/agents/browser/BrowserDeviceToolbar"
import { BrowserViewportResizeHandles } from "@/features/agents/browser/BrowserViewportResizeHandles"
import type {
  BrowserViewport,
  BrowserViewportLayout,
  BrowserViewportSize,
} from "@/features/agents/browser/browserViewport"
import { useBrowserViewportResize } from "@/features/agents/browser/useBrowserViewportResize"

interface Props {
  readonly containerSize: BrowserViewportSize
  readonly viewport: BrowserViewport
  readonly zoomFactor: number
  readonly onViewportChange: (viewport: BrowserViewport) => Promise<void>
  /** Renders the page surface for the resolved layout. */
  readonly children: (layout: BrowserViewportLayout) => ReactNode
}

/**
 * Shared framing for a browser page inside the panel: in fill mode the child
 * gets the whole container; in a fixed viewport it is centred, scaled to fit,
 * and wrapped with the device toolbar and drag rails. Both hosts use it so the
 * two kinds of tab look and resize the same way.
 */
export function BrowserViewportFrame({
  containerSize,
  viewport,
  zoomFactor,
  onViewportChange,
  children,
}: Props) {
  const [aspectRatioLocked, setAspectRatioLocked] = useState(false)
  const fixed = viewport.mode !== "fill"
  const viewportAspectRatio = fixed ? viewport.width / viewport.height : null
  const lockedAspectRatio =
    aspectRatioLocked && viewportAspectRatio !== null
      ? viewportAspectRatio
      : null
  const handleAspectRatioChange = useCallback((aspectRatio: number | null) => {
    setAspectRatioLocked(aspectRatio !== null)
  }, [])
  const {
    activeDrag,
    commitViewportChange,
    effectiveViewport,
    handleResizeKeyDown,
    handleResizePointerDown,
    layout,
  } = useBrowserViewportResize({
    viewport,
    zoomFactor,
    containerSize,
    deviceToolbarVisible: fixed,
    aspectRatio: lockedAspectRatio,
    onCommit: onViewportChange,
  })

  return (
    <div
      className="relative"
      style={{ width: layout.canvasWidth, height: layout.canvasHeight }}
    >
      {fixed && effectiveViewport.mode !== "fill" ? (
        <BrowserDeviceToolbar
          viewport={effectiveViewport}
          width={Math.max(1, Math.round(containerSize.width))}
          aspectRatio={lockedAspectRatio}
          onAspectRatioChange={handleAspectRatioChange}
          onChange={commitViewportChange}
        />
      ) : null}
      {children(layout)}
      {fixed ? (
        <>
          <BrowserViewportResizeHandles
            layout={layout}
            activeDirection={activeDrag?.direction ?? null}
            onPointerDown={handleResizePointerDown}
            onKeyDown={handleResizeKeyDown}
          />
          {activeDrag ? (
            <div
              className="pointer-events-none absolute z-40 -translate-x-1/2 rounded-md border border-border/80 bg-background/95 px-2 py-1 text-[0.625rem] font-medium text-foreground tabular-nums shadow-md backdrop-blur-sm"
              style={{
                left: layout.viewportX + layout.viewportWidth / 2,
                top: layout.viewportY + 10,
              }}
              aria-hidden="true"
            >
              {activeDrag.width} × {activeDrag.height}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
