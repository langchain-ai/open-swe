const PROBE_VARIANTS = [
  "normal 400",
  "normal 700",
  "italic 400",
  "italic 700",
] as const
const PROBE_GLYPHS = ["i", "M", "W", "0", "@", "#", ".", " "] as const
let probeContext: CanvasRenderingContext2D | null | undefined

export function isMonospaceFamily(family: string): boolean {
  try {
    probeContext ??= document.createElement("canvas").getContext("2d")
    if (!probeContext) return true
    for (const variant of PROBE_VARIANTS) {
      probeContext.font = `${variant} 32px ${family}, monospace`
      const widths = PROBE_GLYPHS.map(
        (glyph) => probeContext?.measureText(glyph).width ?? 0
      )
      const first = widths[0]
      if (
        first !== undefined &&
        first > 0 &&
        widths.some((width) => Math.abs(width - first) >= 0.01)
      ) {
        return false
      }
    }
  } catch {
    return true
  }
  return true
}
