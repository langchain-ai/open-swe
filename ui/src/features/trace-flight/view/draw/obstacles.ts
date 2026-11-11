/**
 * Obstacles, drawn inside their collision boxes so what you see is what hits:
 * PR blocks (a diff column with a header lip), terminal windows, failing CI
 * checks (status rows that slide) and merge conflicts (a zipper seam).
 */
import { mix32 } from "../../core/arith"
import { gapAt, type Block, type Obstacle } from "../../core/course"
import { CEILING, FLOOR, GAP_HALF } from "../../core/rules"
import type { Palette } from "../palette"

/** One half of a block: from the course edge to the gap, with the gap edge at `edge`. */
interface Piece {
  readonly x: number
  readonly width: number
  readonly edge: number
  /** +1 when the piece hangs below the gap (grows down), −1 above it. */
  readonly away: 1 | -1
  readonly length: number
}

const LIP = 36
const LINE_GAP = 32
const ROW = 56
const ROW_GAP = 18

function variety(index: number, n: number): number {
  return mix32(mix32(index >>> 0) ^ (n + 11)) / 0x100000000
}

function piecesOf(block: Block, tick: number, scroll: number): Piece[] {
  const x = block.left - scroll
  const gap = gapAt(block, tick)
  const top = gap - GAP_HALF - CEILING
  const bottom = FLOOR - (gap + GAP_HALF)
  return [
    { x, width: block.width, edge: gap - GAP_HALF, away: -1, length: top },
    { x, width: block.width, edge: gap + GAP_HALF, away: 1, length: bottom },
  ].filter((p): p is Piece => p.length > 0)
}

/** A rect spanning `from` to `to` px away from the edge, in either direction. */
function span(piece: Piece, from: number, to: number) {
  const a = piece.edge + piece.away * from
  const b = piece.edge + piece.away * to
  return { y: Math.min(a, b), height: Math.abs(b - a) }
}

function textLines(
  ctx: CanvasRenderingContext2D,
  piece: Piece,
  start: number,
  inset: number,
  seed: number
): void {
  ctx.beginPath()
  for (let k = 0, at = start; at < piece.length - 18; k++, at += LINE_GAP) {
    const y = piece.edge + piece.away * at
    const room = piece.width - 2 * inset
    const length = room * (0.35 + 0.5 * variety(seed, k))
    ctx.moveTo(piece.x + inset, y)
    ctx.lineTo(piece.x + inset + length, y)
  }
  ctx.stroke()
}

function drawPr(
  ctx: CanvasRenderingContext2D,
  piece: Piece,
  palette: Palette,
  seed: number,
  seam: boolean
): void {
  const body = span(piece, seam ? 14 : 0, piece.length)
  ctx.fillStyle = palette.pr
  ctx.fillRect(piece.x + 10, body.y, piece.width - 20, body.height)
  ctx.fillStyle = palette.prLip
  if (seam) {
    // Merge conflict: the halves meet at a zipper of teeth instead of a lip.
    const teeth = 7
    const tooth = piece.width / teeth
    const base = span(piece, 14, 30)
    ctx.fillRect(piece.x, base.y, piece.width, base.height)
    ctx.beginPath()
    for (let t = 0; t < teeth; t++) {
      const x = piece.x + t * tooth
      const root = piece.edge + piece.away * 14
      ctx.moveTo(x, root)
      ctx.lineTo(x + tooth / 2, piece.edge)
      ctx.lineTo(x + tooth, root)
    }
    ctx.fill()
  } else {
    const lip = span(piece, 0, Math.min(LIP, piece.length))
    ctx.beginPath()
    ctx.roundRect(piece.x, lip.y, piece.width, lip.height, 8)
    ctx.fill()
  }
  ctx.strokeStyle = palette.prText
  ctx.lineWidth = 9
  ctx.lineCap = "round"
  textLines(ctx, piece, LIP + 34, 34, seed)
}

function drawTerminal(
  ctx: CanvasRenderingContext2D,
  piece: Piece,
  palette: Palette,
  seed: number
): void {
  const body = span(piece, 0, piece.length)
  // Only the corners at the gap are rounded; the far end runs off the screen.
  const corners = piece.away === 1 ? [16, 16, 0, 0] : [0, 0, 16, 16]
  ctx.fillStyle = palette.terminal
  ctx.beginPath()
  ctx.roundRect(piece.x, body.y, piece.width, body.height, corners)
  ctx.fill()
  const bar = span(piece, 0, Math.min(48, piece.length))
  ctx.fillStyle = palette.terminalBar
  ctx.beginPath()
  ctx.roundRect(piece.x, bar.y, piece.width, bar.height, corners)
  ctx.fill()
  ctx.fillStyle = palette.terminalText
  for (let d = 0; d < 3; d++) {
    ctx.beginPath()
    ctx.arc(piece.x + 28 + d * 26, bar.y + bar.height / 2, 7, 0, 2 * Math.PI)
    ctx.fill()
  }
  if (piece.length < 120) return
  const prompt = piece.edge + piece.away * 86
  ctx.strokeStyle = palette.terminalText
  ctx.lineWidth = 7
  ctx.lineCap = "round"
  ctx.lineJoin = "round"
  ctx.beginPath()
  ctx.moveTo(piece.x + 26, prompt - 14)
  ctx.lineTo(piece.x + 42, prompt)
  ctx.lineTo(piece.x + 26, prompt + 14)
  ctx.stroke()
  ctx.fillRect(piece.x + 54, prompt - 16, 18, 32)
  ctx.lineWidth = 9
  textLines(ctx, piece, 136, 30, seed)
}

function drawCi(
  ctx: CanvasRenderingContext2D,
  piece: Piece,
  palette: Palette,
  seed: number
): void {
  for (let r = 0, at = 0; at < piece.length; r++, at += ROW + ROW_GAP) {
    const row = span(piece, at, Math.min(at + ROW, piece.length))
    const failing = variety(seed, r) < 0.22
    ctx.fillStyle = failing ? palette.ciFail : palette.ci
    ctx.beginPath()
    ctx.roundRect(piece.x, row.y, piece.width, row.height, 12)
    ctx.fill()
    if (row.height < ROW) continue
    const cy = row.y + ROW / 2
    const cx = piece.x + 30
    // A failing row is darker, so its badge and bar step darker with it.
    const mark = failing ? palette.ciFailMark : palette.ciBadge
    ctx.fillStyle = mark
    ctx.beginPath()
    ctx.arc(cx, cy, 15, 0, 2 * Math.PI)
    ctx.fill()
    ctx.strokeStyle = palette.ciMark
    ctx.lineWidth = 4
    ctx.lineCap = "round"
    ctx.beginPath()
    if (failing) {
      ctx.moveTo(cx - 6, cy - 6)
      ctx.lineTo(cx + 6, cy + 6)
      ctx.moveTo(cx + 6, cy - 6)
      ctx.lineTo(cx - 6, cy + 6)
    } else {
      ctx.moveTo(cx - 7, cy)
      ctx.lineTo(cx - 2, cy + 5)
      ctx.lineTo(cx + 7, cy - 5)
    }
    ctx.stroke()
    ctx.strokeStyle = mark
    ctx.lineWidth = 10
    ctx.beginPath()
    ctx.moveTo(cx + 30, cy)
    ctx.lineTo(piece.x + piece.width - 22, cy)
    ctx.stroke()
  }
}

/** Arrows beside a Failing CI block: its gap slides up and down. */
function drawSlideHint(
  ctx: CanvasRenderingContext2D,
  block: Block,
  tick: number,
  scroll: number,
  palette: Palette
): void {
  const x = block.left - scroll + block.width + 46
  const gap = gapAt(block, tick)
  ctx.strokeStyle = palette.prLip
  ctx.lineWidth = 5
  ctx.lineCap = "round"
  ctx.lineJoin = "round"
  ctx.beginPath()
  for (const dir of [-1, 1]) {
    const tip = gap + dir * 120
    const tail = gap + dir * 40
    ctx.moveTo(x, tail)
    ctx.lineTo(x, tip)
    ctx.moveTo(x - 14, tip - dir * 14)
    ctx.lineTo(x, tip)
    ctx.lineTo(x + 14, tip - dir * 14)
  }
  ctx.stroke()
}

export function drawObstacle(
  ctx: CanvasRenderingContext2D,
  item: Obstacle,
  tick: number,
  scroll: number,
  palette: Palette
): void {
  for (const [b, block] of item.blocks.entries()) {
    for (const piece of piecesOf(block, tick, scroll)) {
      const seed = item.index * 8 + b * 2 + (piece.away === 1 ? 1 : 0)
      if (item.kind === "terminal") drawTerminal(ctx, piece, palette, seed)
      else if (item.kind === "ci") drawCi(ctx, piece, palette, seed)
      else drawPr(ctx, piece, palette, seed, item.kind === "conflict")
    }
    if (item.kind === "ci") drawSlideHint(ctx, block, tick, scroll, palette)
  }
}
