/** Matches a canvas backing store to its CSS size and device pixel ratio. */
export function sizeCanvas(
  canvas: HTMLCanvasElement
): { ctx: CanvasRenderingContext2D; width: number; height: number } | null {
  const ctx = canvas.getContext("2d")
  if (!ctx) return null
  const dpr = window.devicePixelRatio || 1
  const width = canvas.clientWidth
  const height = canvas.clientHeight
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
    canvas.width = width * dpr
    canvas.height = height * dpr
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return { ctx, width, height }
}
