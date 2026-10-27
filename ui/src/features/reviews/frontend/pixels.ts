export interface PixelRect {
  x: number
  y: number
  width: number
  height: number
}

export interface PixelRegion extends PixelRect {
  changedPixels: number
}

export interface PixelOptions {
  threshold?: number
  maskRects?: PixelRect[]
  tileSize?: number
  minRegionPixels?: number
}

export interface PixelComparison {
  width: number
  height: number
  changedPixels: number
  totalPixels: number
  regions: PixelRegion[]
  mask: Uint8ClampedArray
  diff: Uint8ClampedArray
}

interface Tile {
  left: number
  top: number
  right: number
  bottom: number
  changedPixels: number
}

export function comparePixels(
  before: Uint8ClampedArray,
  after: Uint8ClampedArray,
  width: number,
  height: number,
  {
    threshold = 18,
    maskRects = [],
    tileSize = 24,
    minRegionPixels = 4,
  }: PixelOptions = {}
): PixelComparison {
  if (
    !Number.isSafeInteger(width) ||
    !Number.isSafeInteger(height) ||
    width < 1 ||
    height < 1 ||
    width * height > 16_000_000
  )
    throw new Error("Screenshots must contain between 1 and 16 million pixels.")
  if (before.length !== width * height * 4 || after.length !== before.length)
    throw new Error("Screenshots must have identical dimensions.")
  if (
    !Number.isFinite(threshold) ||
    threshold < 0 ||
    threshold > 255 ||
    !Number.isSafeInteger(tileSize) ||
    tileSize < 1 ||
    !Number.isSafeInteger(minRegionPixels) ||
    minRegionPixels < 1
  )
    throw new Error("Invalid pixel comparison settings.")
  const excluded = new Uint8Array(width * height)
  for (const rect of maskRects) {
    if (
      ![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite) ||
      rect.width < 0 ||
      rect.height < 0
    )
      throw new Error("Invalid exclusion rectangle.")
    const left = Math.max(0, Math.ceil(rect.x))
    const right = Math.min(width, Math.ceil(rect.x + rect.width))
    for (
      let y = Math.max(0, Math.ceil(rect.y));
      y < Math.min(height, Math.ceil(rect.y + rect.height));
      y++
    )
      if (left < right) excluded.fill(1, y * width + left, y * width + right)
  }
  const mask = new Uint8ClampedArray(before.length)
  const diff = new Uint8ClampedArray(before.length)
  const columns = Math.ceil(width / tileSize)
  const rows = Math.ceil(height / tileSize)
  const tiles = new Map<number, Tile>()
  let changedPixels = 0
  let totalPixels = 0
  for (let i = 0; i < before.length; i += 4) {
    const index = i / 4
    if (excluded[index]) {
      diff.set([225, 229, 235, 255], i)
      continue
    }
    totalPixels++
    const changed = [0, 1, 2, 3].some(
      (channel) =>
        Math.abs(before[i + channel]! - after[i + channel]!) > threshold
    )
    if (changed) {
      changedPixels++
      mask.set([226, 61, 117, 255], i)
      diff.set([226, 61, 117, 255], i)
      const x = index % width
      const y = Math.floor(index / width)
      const key = Math.floor(y / tileSize) * columns + Math.floor(x / tileSize)
      const tile = tiles.get(key)
      if (tile) {
        tile.left = Math.min(tile.left, x)
        tile.top = Math.min(tile.top, y)
        tile.right = Math.max(tile.right, x)
        tile.bottom = Math.max(tile.bottom, y)
        tile.changedPixels++
      } else
        tiles.set(key, {
          left: x,
          top: y,
          right: x,
          bottom: y,
          changedPixels: 1,
        })
    } else {
      const alpha = before[i + 3]! / 255
      const luminance =
        (before[i]! * 0.2126 +
          before[i + 1]! * 0.7152 +
          before[i + 2]! * 0.0722) *
          alpha +
        255 * (1 - alpha)
      const faded = Math.round(255 * 0.7 + luminance * 0.3)
      diff.set([faded, faded, faded, 255], i)
    }
  }
  const remaining = new Set(tiles.keys())
  const regions: PixelRegion[] = []
  for (const start of remaining) {
    remaining.delete(start)
    const queue = [start]
    let left = width,
      top = height,
      right = 0,
      bottom = 0,
      count = 0
    for (let cursor = 0; cursor < queue.length; cursor++) {
      const key = queue[cursor]!
      const tile = tiles.get(key)!
      left = Math.min(left, tile.left)
      top = Math.min(top, tile.top)
      right = Math.max(right, tile.right)
      bottom = Math.max(bottom, tile.bottom)
      count += tile.changedPixels
      const x = key % columns,
        y = Math.floor(key / columns)
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          if (x + dx < 0 || x + dx >= columns || y + dy < 0 || y + dy >= rows)
            continue
          const neighbor = (y + dy) * columns + x + dx
          if (remaining.delete(neighbor)) queue.push(neighbor)
        }
    }
    if (count >= minRegionPixels)
      regions.push({
        x: left,
        y: top,
        width: right - left + 1,
        height: bottom - top + 1,
        changedPixels: count,
      })
  }
  regions.sort(
    (a, b) => b.changedPixels - a.changedPixels || a.y - b.y || a.x - b.x
  )
  return {
    width,
    height,
    changedPixels,
    totalPixels,
    regions: regions.slice(0, 100),
    mask,
    diff,
  }
}

export function cropRegion(
  region: PixelRect,
  width: number,
  height: number
): PixelRect {
  const cropWidth = Math.min(width, Math.max(160, region.width + 48))
  const cropHeight = Math.min(height, Math.max(120, region.height + 48))
  return {
    x: Math.max(
      0,
      Math.min(
        width - cropWidth,
        Math.floor(region.x + region.width / 2 - cropWidth / 2)
      )
    ),
    y: Math.max(
      0,
      Math.min(
        height - cropHeight,
        Math.floor(region.y + region.height / 2 - cropHeight / 2)
      )
    ),
    width: cropWidth,
    height: cropHeight,
  }
}
