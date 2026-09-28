/**
 * Viewport model for browser tabs: either the tab fills the panel, or it is
 * pinned to a fixed CSS size (a device preset or a free-form size) that the
 * panel scales down to fit. The geometry here is shared by both hosts.
 */

export interface BrowserViewportSize {
  readonly width: number
  readonly height: number
}

export const BROWSER_VIEWPORT_PRESET_IDS = [
  "iphone-se",
  "iphone-xr",
  "iphone-12-pro",
  "iphone-14-pro-max",
  "pixel-7",
  "samsung-galaxy-s8-plus",
  "samsung-galaxy-s20-ultra",
  "ipad-mini",
  "ipad-air",
  "ipad-pro",
  "surface-pro-7",
  "surface-duo",
  "galaxy-z-fold-5",
  "asus-zenbook-fold",
  "samsung-galaxy-a51-71",
  "nest-hub",
  "nest-hub-max",
] as const
export type BrowserViewportPresetId =
  (typeof BROWSER_VIEWPORT_PRESET_IDS)[number]

export type BrowserViewport =
  | { readonly mode: "fill" }
  | {
      readonly mode: "freeform"
      readonly width: number
      readonly height: number
    }
  | {
      readonly mode: "preset"
      readonly width: number
      readonly height: number
      readonly presetId: BrowserViewportPresetId
    }

export type FixedBrowserViewport = Exclude<BrowserViewport, { mode: "fill" }>

export const FILL_VIEWPORT: BrowserViewport = { mode: "fill" }

export const BROWSER_VIEWPORT_MIN_DIMENSION = 240
export const BROWSER_VIEWPORT_MAX_DIMENSION = 3840
export const BROWSER_VIEWPORT_MAX_AREA = 3840 * 2160

export interface BrowserViewportPreset extends BrowserViewportSize {
  readonly id: BrowserViewportPresetId
  readonly label: string
  readonly category: "Tablet" | "Phone"
}

// Chrome DevTools' default device order; sizes are CSS viewport sizes.
const PRESET_DEFINITIONS: Record<
  BrowserViewportPresetId,
  Omit<BrowserViewportPreset, "id">
> = {
  "iphone-se": {
    label: "iPhone SE",
    category: "Phone",
    width: 375,
    height: 667,
  },
  "iphone-xr": {
    label: "iPhone XR",
    category: "Phone",
    width: 414,
    height: 896,
  },
  "iphone-12-pro": {
    label: "iPhone 12 Pro",
    category: "Phone",
    width: 390,
    height: 844,
  },
  "iphone-14-pro-max": {
    label: "iPhone 14 Pro Max",
    category: "Phone",
    width: 430,
    height: 932,
  },
  "pixel-7": { label: "Pixel 7", category: "Phone", width: 412, height: 915 },
  "samsung-galaxy-s8-plus": {
    label: "Samsung Galaxy S8+",
    category: "Phone",
    width: 360,
    height: 740,
  },
  "samsung-galaxy-s20-ultra": {
    label: "Samsung Galaxy S20 Ultra",
    category: "Phone",
    width: 412,
    height: 915,
  },
  "ipad-mini": {
    label: "iPad Mini",
    category: "Tablet",
    width: 768,
    height: 1024,
  },
  "ipad-air": {
    label: "iPad Air",
    category: "Tablet",
    width: 820,
    height: 1180,
  },
  "ipad-pro": {
    label: "iPad Pro",
    category: "Tablet",
    width: 1024,
    height: 1366,
  },
  "surface-pro-7": {
    label: "Surface Pro 7",
    category: "Tablet",
    width: 912,
    height: 1368,
  },
  "surface-duo": {
    label: "Surface Duo",
    category: "Phone",
    width: 540,
    height: 720,
  },
  "galaxy-z-fold-5": {
    label: "Galaxy Z Fold 5",
    category: "Phone",
    width: 344,
    height: 882,
  },
  "asus-zenbook-fold": {
    label: "Asus Zenbook Fold",
    category: "Tablet",
    width: 853,
    height: 1280,
  },
  "samsung-galaxy-a51-71": {
    label: "Samsung Galaxy A51/71",
    category: "Phone",
    width: 412,
    height: 914,
  },
  "nest-hub": {
    label: "Nest Hub",
    category: "Tablet",
    width: 1024,
    height: 600,
  },
  "nest-hub-max": {
    label: "Nest Hub Max",
    category: "Tablet",
    width: 1280,
    height: 800,
  },
}

export const BROWSER_VIEWPORT_PRESETS: ReadonlyArray<BrowserViewportPreset> =
  BROWSER_VIEWPORT_PRESET_IDS.map((id) => ({ id, ...PRESET_DEFINITIONS[id] }))

export function isBrowserViewportPresetId(
  value: unknown
): value is BrowserViewportPresetId {
  return (
    typeof value === "string" &&
    (BROWSER_VIEWPORT_PRESET_IDS as ReadonlyArray<string>).includes(value)
  )
}

export function presetViewport(
  presetId: BrowserViewportPresetId,
  orientation: "portrait" | "landscape" = "portrait"
): BrowserViewport {
  const preset = PRESET_DEFINITIONS[presetId]
  const nativePortrait = preset.height >= preset.width
  const swap =
    (orientation === "landscape" && nativePortrait) ||
    (orientation === "portrait" && !nativePortrait)
  return {
    mode: "preset",
    presetId,
    width: swap ? preset.height : preset.width,
    height: swap ? preset.width : preset.height,
  }
}

export function isValidViewportSize(size: BrowserViewportSize): boolean {
  return (
    Number.isInteger(size.width) &&
    Number.isInteger(size.height) &&
    size.width >= BROWSER_VIEWPORT_MIN_DIMENSION &&
    size.width <= BROWSER_VIEWPORT_MAX_DIMENSION &&
    size.height >= BROWSER_VIEWPORT_MIN_DIMENSION &&
    size.height <= BROWSER_VIEWPORT_MAX_DIMENSION &&
    size.width * size.height <= BROWSER_VIEWPORT_MAX_AREA
  )
}

/** Re-validates a viewport read from persisted or remote state. */
export function parseBrowserViewport(value: unknown): BrowserViewport {
  if (!value || typeof value !== "object") return FILL_VIEWPORT
  const record = value as Record<string, unknown>
  if (record.mode === "fill") return FILL_VIEWPORT
  if (record.mode !== "freeform" && record.mode !== "preset")
    return FILL_VIEWPORT
  if (typeof record.width !== "number" || typeof record.height !== "number") {
    return FILL_VIEWPORT
  }
  const size = { width: record.width, height: record.height }
  if (!isValidViewportSize(size)) return FILL_VIEWPORT
  if (record.mode === "preset") {
    return isBrowserViewportPresetId(record.presetId)
      ? { mode: "preset", presetId: record.presetId, ...size }
      : { mode: "freeform", ...size }
  }
  return { mode: "freeform", ...size }
}

export const browserViewportKey = (viewport: BrowserViewport): string =>
  viewport.mode === "fill"
    ? "fill"
    : `${viewport.mode}:${viewport.width}:${viewport.height}:${
        viewport.mode === "preset" ? viewport.presetId : ""
      }`

/** Discrete zoom ladder mirroring Chrome's. */
export const BROWSER_ZOOM_LEVELS = [
  0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4,
  5,
] as const
export const DEFAULT_BROWSER_ZOOM = 1
const ZOOM_EPSILON = 0.001

export function normalizeZoomFactor(zoomFactor: number): number {
  return Number.isFinite(zoomFactor) && zoomFactor > 0 ? zoomFactor : 1
}

export function stepBrowserZoom(current: number, direction: 1 | -1): number {
  const levels = BROWSER_ZOOM_LEVELS
  const index = levels.findIndex(
    (level) => Math.abs(level - current) < ZOOM_EPSILON
  )
  if (index === -1) {
    const nearest = levels.reduce((best, level) =>
      Math.abs(level - current) < Math.abs(best - current) ? level : best
    )
    return direction > 0
      ? (levels.find((level) => level > current) ?? nearest)
      : ([...levels].reverse().find((level) => level < current) ?? nearest)
  }
  return levels[Math.min(levels.length - 1, Math.max(0, index + direction))]!
}

export interface BrowserViewportLayout {
  readonly canvasWidth: number
  readonly canvasHeight: number
  readonly viewportX: number
  readonly viewportY: number
  /** Visible footprint inside the panel after fit-to-panel scaling. */
  readonly viewportWidth: number
  readonly viewportHeight: number
  /** Presentation-only scale; the page keeps its requested CSS viewport. */
  readonly viewportScale: number
  readonly fillsPanel: boolean
}

export const BROWSER_DEVICE_TOOLBAR_HEIGHT = 32
export const BROWSER_VIEWPORT_RESIZE_RAIL_SIZE = 10

export type BrowserViewportResizeDirection =
  | "north"
  | "northeast"
  | "east"
  | "southeast"
  | "south"
  | "southwest"
  | "west"
  | "northwest"

/** Area left for the page once the device toolbar and drag rails take theirs. */
export function resolveBrowserDeviceViewportArea(
  container: BrowserViewportSize
): BrowserViewportSize {
  return {
    width: Math.max(1, container.width - BROWSER_VIEWPORT_RESIZE_RAIL_SIZE * 2),
    height: Math.max(
      1,
      container.height -
        BROWSER_DEVICE_TOOLBAR_HEIGHT -
        BROWSER_VIEWPORT_RESIZE_RAIL_SIZE
    ),
  }
}

export function resolveBrowserViewportLayout(
  container: BrowserViewportSize,
  viewport: BrowserViewport,
  zoomFactor = 1
): BrowserViewportLayout {
  const containerWidth = Math.max(1, Math.round(container.width))
  const containerHeight = Math.max(1, Math.round(container.height))
  if (viewport.mode === "fill") {
    return {
      canvasWidth: containerWidth,
      canvasHeight: containerHeight,
      viewportX: 0,
      viewportY: 0,
      viewportWidth: containerWidth,
      viewportHeight: containerHeight,
      viewportScale: 1,
      fillsPanel: true,
    }
  }
  const zoom = normalizeZoomFactor(zoomFactor)
  const renderedWidth = viewport.width * zoom
  const renderedHeight = viewport.height * zoom
  const viewportScale = Math.min(
    1,
    containerWidth / renderedWidth,
    containerHeight / renderedHeight
  )
  const viewportWidth = renderedWidth * viewportScale
  const viewportHeight = renderedHeight * viewportScale
  return {
    canvasWidth: containerWidth,
    canvasHeight: containerHeight,
    viewportX: Math.max(0, Math.round((containerWidth - viewportWidth) / 2)),
    viewportY: Math.max(0, Math.round((containerHeight - viewportHeight) / 2)),
    viewportWidth,
    viewportHeight,
    viewportScale,
    fillsPanel: false,
  }
}

export function resolveBrowserDeviceViewportLayout(
  container: BrowserViewportSize,
  viewport: FixedBrowserViewport,
  zoomFactor = 1
): BrowserViewportLayout {
  const layout = resolveBrowserViewportLayout(
    resolveBrowserDeviceViewportArea(container),
    viewport,
    zoomFactor
  )
  return {
    ...layout,
    canvasWidth: Math.max(1, Math.round(container.width)),
    canvasHeight: Math.max(1, Math.round(container.height)),
    viewportX: layout.viewportX + BROWSER_VIEWPORT_RESIZE_RAIL_SIZE,
    viewportY: layout.viewportY + BROWSER_DEVICE_TOOLBAR_HEIGHT,
  }
}

const clampDimension = (value: number): number =>
  Math.min(
    BROWSER_VIEWPORT_MAX_DIMENSION,
    Math.max(BROWSER_VIEWPORT_MIN_DIMENSION, value)
  )

const validAspectRatio = (ratio: number | undefined): ratio is number =>
  ratio !== undefined && Number.isFinite(ratio) && ratio > 0

function resizeAtAspectRatio(
  desired: number,
  aspectRatio: number,
  primaryAxis: "width" | "height"
): BrowserViewportSize {
  if (primaryAxis === "width") {
    const minimum = Math.ceil(
      Math.max(
        BROWSER_VIEWPORT_MIN_DIMENSION,
        BROWSER_VIEWPORT_MIN_DIMENSION * aspectRatio
      )
    )
    const maximum = Math.floor(
      Math.min(
        BROWSER_VIEWPORT_MAX_DIMENSION,
        BROWSER_VIEWPORT_MAX_DIMENSION * aspectRatio,
        Math.sqrt(BROWSER_VIEWPORT_MAX_AREA * aspectRatio)
      )
    )
    let width = Math.min(maximum, Math.max(minimum, Math.round(desired)))
    let height = Math.round(width / aspectRatio)
    while (width * height > BROWSER_VIEWPORT_MAX_AREA && width > minimum) {
      width -= 1
      height = Math.round(width / aspectRatio)
    }
    return { width, height }
  }
  const minimum = Math.ceil(
    Math.max(
      BROWSER_VIEWPORT_MIN_DIMENSION,
      BROWSER_VIEWPORT_MIN_DIMENSION / aspectRatio
    )
  )
  const maximum = Math.floor(
    Math.min(
      BROWSER_VIEWPORT_MAX_DIMENSION,
      BROWSER_VIEWPORT_MAX_DIMENSION / aspectRatio,
      Math.sqrt(BROWSER_VIEWPORT_MAX_AREA / aspectRatio)
    )
  )
  let height = Math.min(maximum, Math.max(minimum, Math.round(desired)))
  let width = Math.round(height * aspectRatio)
  while (width * height > BROWSER_VIEWPORT_MAX_AREA && height > minimum) {
    height -= 1
    width = Math.round(height * aspectRatio)
  }
  return { width, height }
}

/** Applies a pointer delta (in rendered pixels) to a fixed viewport. */
export function resizeFreeformViewport(
  start: BrowserViewportSize,
  delta: { readonly x: number; readonly y: number },
  zoomFactor = 1,
  direction: BrowserViewportResizeDirection = "southeast",
  aspectRatio?: number
): BrowserViewportSize {
  const zoom = normalizeZoomFactor(zoomFactor)
  const horizontalDelta = direction.includes("east")
    ? delta.x
    : direction.includes("west")
      ? -delta.x
      : 0
  const verticalDelta = direction.includes("south")
    ? delta.y
    : direction.includes("north")
      ? -delta.y
      : 0
  const desiredWidth = start.width + horizontalDelta / zoom
  const desiredHeight = start.height + verticalDelta / zoom
  if (validAspectRatio(aspectRatio)) {
    const controlsWidth =
      horizontalDelta !== 0 || direction === "east" || direction === "west"
    const controlsHeight =
      verticalDelta !== 0 || direction === "north" || direction === "south"
    const primaryAxis =
      controlsWidth && !controlsHeight
        ? "width"
        : controlsHeight && !controlsWidth
          ? "height"
          : Math.abs(desiredWidth - start.width) / start.width >=
              Math.abs(desiredHeight - start.height) / start.height
            ? "width"
            : "height"
    return resizeAtAspectRatio(
      primaryAxis === "width" ? desiredWidth : desiredHeight,
      aspectRatio,
      primaryAxis
    )
  }
  let width = clampDimension(Math.round(desiredWidth))
  let height = clampDimension(Math.round(desiredHeight))
  if (width * height <= BROWSER_VIEWPORT_MAX_AREA) return { width, height }
  if (Math.abs(horizontalDelta) >= Math.abs(verticalDelta)) {
    width = Math.max(
      BROWSER_VIEWPORT_MIN_DIMENSION,
      Math.floor(BROWSER_VIEWPORT_MAX_AREA / height)
    )
  } else {
    height = Math.max(
      BROWSER_VIEWPORT_MIN_DIMENSION,
      Math.floor(BROWSER_VIEWPORT_MAX_AREA / width)
    )
  }
  return { width, height }
}

const resizeFromEndRail = (
  start: number,
  pointerDelta: number,
  available: number
): number => {
  const startEdge = start < available ? (available + start) / 2 : start
  const targetEdge = startEdge + pointerDelta
  return targetEdge <= available ? targetEdge * 2 - available : targetEdge
}

const resizeFromStartRail = (
  start: number,
  pointerDelta: number,
  available: number
): number => {
  if (start > available) {
    const distanceToFit = start - available
    return pointerDelta <= distanceToFit
      ? start - pointerDelta
      : available - (pointerDelta - distanceToFit) * 2
  }
  const targetEdge = (available - start) / 2 + pointerDelta
  return targetEdge >= 0 ? available - targetEdge * 2 : available - targetEdge
}

/**
 * Drag resizing from a rail: a centred viewport grows twice as fast as the
 * pointer moves (both edges move), while one wider than the panel grows from
 * the dragged edge only.
 */
export function resizeBrowserViewportFromRail(
  start: BrowserViewportSize,
  pointerDelta: { readonly x: number; readonly y: number },
  available: BrowserViewportSize,
  zoomFactor = 1,
  direction: BrowserViewportResizeDirection = "southeast",
  aspectRatio?: number
): BrowserViewportSize {
  const zoom = normalizeZoomFactor(zoomFactor)
  const startWidth = start.width * zoom
  const startHeight = start.height * zoom
  const desiredWidth = direction.includes("east")
    ? resizeFromEndRail(startWidth, pointerDelta.x, available.width)
    : direction.includes("west")
      ? resizeFromStartRail(startWidth, pointerDelta.x, available.width)
      : startWidth
  const desiredHeight = direction.includes("south")
    ? resizeFromEndRail(startHeight, pointerDelta.y, available.height)
    : direction.includes("north")
      ? resizeFromStartRail(startHeight, pointerDelta.y, available.height)
      : startHeight
  const widthDelta = desiredWidth - startWidth
  const heightDelta = desiredHeight - startHeight
  return resizeFreeformViewport(
    start,
    {
      x: direction.includes("west") ? -widthDelta : widthDelta,
      y: direction.includes("north") ? -heightDelta : heightDelta,
    },
    zoom,
    direction,
    aspectRatio
  )
}

/** The fixed size that exactly fits the panel when the device toolbar turns on. */
export function resolveResponsiveBrowserViewportSize(
  container: BrowserViewportSize,
  zoomFactor = 1
): BrowserViewportSize {
  const area = resolveBrowserDeviceViewportArea(container)
  const zoom = normalizeZoomFactor(zoomFactor)
  return resizeFreeformViewport(
    { width: area.width / zoom, height: area.height / zoom },
    { x: 0, y: 0 }
  )
}
