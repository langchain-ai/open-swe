/** Decoded RGBA pixels, as a canvas returns them. */
export type Pixels = Pick<ImageData, "width" | "height" | "data">

/** Past this many pixels a pair is too heavy to decode and compare in the page. */
const MAX_PIXELS = 16_000_000

/**
 * How many pixels of two screenshots of one size differ, in any channel: the
 * extent of a change, never its severity.
 */
export class PixelChange {
  private constructor(
    readonly changedPixels: number,
    readonly totalPixels: number
  ) {}

  /** The share of pixels that changed, from 0 to 1. */
  get ratio(): number {
    return this.changedPixels / this.totalPixels
  }

  /** Compares two images pixel for pixel; null when their sizes differ. */
  static of(before: Pixels, after: Pixels): PixelChange | null {
    if (before.width !== after.width || before.height !== after.height)
      return null
    const total = before.width * before.height
    if (total === 0) return null
    // One 32-bit word per RGBA pixel, so a pixel differs when its word does.
    const first = new Uint32Array(
      before.data.buffer,
      before.data.byteOffset,
      total
    )
    const second = new Uint32Array(
      after.data.buffer,
      after.data.byteOffset,
      total
    )
    let changed = 0
    for (let index = 0; index < total; index++)
      if (first[index] !== second[index]) changed++
    return new PixelChange(changed, total)
  }

  /** Decodes two screenshots and compares them; null when they can't be compared. */
  static async between(before: Blob, after: Blob): Promise<PixelChange | null> {
    const [first, second] = await Promise.all([
      PixelChange.decoded(before),
      PixelChange.decoded(after),
    ])
    return first && second ? PixelChange.of(first, second) : null
  }

  private static async decoded(blob: Blob): Promise<Pixels | null> {
    // As stored, so a color profile can't make equal pixels differ.
    const bitmap = await createImageBitmap(blob, {
      colorSpaceConversion: "none",
      premultiplyAlpha: "none",
    })
    try {
      if (bitmap.width * bitmap.height > MAX_PIXELS) return null
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height)
      const context = canvas.getContext("2d")
      if (!context) return null
      context.drawImage(bitmap, 0, 0)
      return context.getImageData(0, 0, bitmap.width, bitmap.height)
    } finally {
      bitmap.close()
    }
  }
}
