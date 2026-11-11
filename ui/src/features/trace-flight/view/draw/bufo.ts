/**
 * Bufo cameos, drawn behind the obstacles in grayscale so only the bird and the
 * trace keep their color. Sheets load on first sight; a Bufo whose sheet isn't
 * ready yet is simply skipped for that frame.
 */
import { levelOf } from "../../core/course"
import { FLOOR, LEAD, SPACING, WORLD_WIDTH } from "../../core/rules"
import { BUFOS, cameoOf, frameAt, type BufoName } from "../bufos"

/** How tall a Bufo stands, world units. */
const HEIGHT = 124
/** Course x past an obstacle's left edge: the open air after even the widest one. */
const OFFSET = 540

const sheets = new Map<BufoName, HTMLImageElement>()

function sheetFor(name: BufoName): HTMLImageElement | null {
  let image = sheets.get(name)
  if (!image) {
    image = new Image()
    image.src = BUFOS[name].url
    sheets.set(name, image)
  }
  return image.complete && image.naturalWidth > 0 ? image : null
}

export function drawBufos(
  ctx: CanvasRenderingContext2D,
  seed: number,
  firstIndex: number,
  lastIndex: number,
  scroll: number,
  tick: number,
  reducedMotion: boolean
): void {
  for (let level = levelOf(firstIndex); level <= levelOf(lastIndex); level++) {
    const cameo = cameoOf(seed, level)
    const sheet = BUFOS[cameo.bufo]
    const scale = HEIGHT / sheet.height
    const width = sheet.width * scale
    const x = LEAD + cameo.index * SPACING + OFFSET - scroll - width / 2
    if (x + width < 0 || x > WORLD_WIDTH) continue
    const image = sheetFor(cameo.bufo)
    if (!image) continue
    const frame = reducedMotion ? 0 : frameAt(sheet, tick)
    ctx.save()
    ctx.globalAlpha = 0.9
    ctx.drawImage(
      image,
      frame * sheet.width,
      0,
      sheet.width,
      sheet.height,
      x,
      FLOOR - HEIGHT + 4,
      width,
      HEIGHT
    )
    ctx.restore()
  }
}
