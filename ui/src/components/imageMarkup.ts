import type { ImageChunk } from "@/features/agents/lib/types"

export type MarkupTool = "ellipse" | "arrow"

/** A shape drawn from where the drag started to where it ended, in image pixels. */
export interface MarkupShape {
  tool: MarkupTool
  x1: number
  y1: number
  x2: number
  y2: number
}

type Point = readonly [number, number]

/** Baked into the exported pixels, so it is a fixed ink rather than a theme token. */
export const MARKUP_INK = "#ff3b30"

const MAX_EXPORT_BYTES = 10 * 1024 * 1024

/** Circles and arrows drawn over an image of a known size, and the image they make. */
export class ImageMarkup {
  constructor(
    readonly width: number,
    readonly height: number,
    readonly shapes: ReadonlyArray<MarkupShape> = []
  ) {}

  get stroke(): number {
    return Math.max(3, Math.round(Math.max(this.width, this.height) / 250))
  }

  with(shape: MarkupShape): ImageMarkup {
    return new ImageMarkup(this.width, this.height, [...this.shapes, shape])
  }

  undo(): ImageMarkup {
    return new ImageMarkup(this.width, this.height, this.shapes.slice(0, -1))
  }

  clear(): ImageMarkup {
    return new ImageMarkup(this.width, this.height)
  }

  /** Whether a drag covered enough ground to be a deliberate shape. */
  isDeliberate(shape: MarkupShape): boolean {
    return (
      Math.hypot(shape.x2 - shape.x1, shape.y2 - shape.y1) > this.stroke * 3
    )
  }

  /** The two barbs of an arrow's head, which sits where the drag ended. */
  private arrowHead(shape: MarkupShape): [Point, Point] {
    const angle = Math.atan2(shape.y2 - shape.y1, shape.x2 - shape.x1)
    const length = this.stroke * 5
    const barb = (offset: number): Point => [
      shape.x2 - length * Math.cos(angle + offset),
      shape.y2 - length * Math.sin(angle + offset),
    ]
    return [barb(Math.PI / 7), barb(-Math.PI / 7)]
  }

  /** An arrow as an SVG path: its shaft, then its head. */
  arrowPath(shape: MarkupShape): string {
    const [left, right] = this.arrowHead(shape)
    return `M${shape.x1} ${shape.y1}L${shape.x2} ${shape.y2}M${left[0]} ${left[1]}L${shape.x2} ${shape.y2}L${right[0]} ${right[1]}`
  }

  /** The image behind `source` with these shapes drawn on it, ready to attach. */
  async render(source: Blob, fileName: string): Promise<ImageChunk> {
    const bitmap = await createImageBitmap(source)
    const canvas = document.createElement("canvas")
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const context = canvas.getContext("2d")
    if (!context) throw new Error("Canvas 2D is unavailable")
    context.drawImage(bitmap, 0, 0)
    bitmap.close()
    context.scale(canvas.width / this.width, canvas.height / this.height)
    context.strokeStyle = MARKUP_INK
    context.lineWidth = this.stroke
    context.lineCap = "round"
    context.lineJoin = "round"
    for (const shape of this.shapes) {
      context.beginPath()
      if (shape.tool === "ellipse") {
        context.ellipse(
          (shape.x1 + shape.x2) / 2,
          (shape.y1 + shape.y2) / 2,
          Math.abs(shape.x2 - shape.x1) / 2,
          Math.abs(shape.y2 - shape.y1) / 2,
          0,
          0,
          Math.PI * 2
        )
      } else {
        const [left, right] = this.arrowHead(shape)
        context.moveTo(shape.x1, shape.y1)
        context.lineTo(shape.x2, shape.y2)
        context.moveTo(...left)
        context.lineTo(shape.x2, shape.y2)
        context.lineTo(...right)
      }
      context.stroke()
    }
    let mimeType = "image/png"
    let blob = await this.encode(canvas, mimeType)
    if (blob.size > MAX_EXPORT_BYTES) {
      mimeType = "image/jpeg"
      blob = await this.encode(canvas, mimeType, 0.85)
    }
    const base64 = await this.base64(blob)
    const stem = fileName.replace(/\.[^.]+$/, "") || "image"
    return {
      kind: "image",
      base64,
      mimeType,
      fileName: `${stem}-annotated.${mimeType === "image/png" ? "png" : "jpg"}`,
    }
  }

  private encode(
    canvas: HTMLCanvasElement,
    mimeType: string,
    quality?: number
  ): Promise<Blob> {
    return new Promise((resolve, reject) =>
      canvas.toBlob(
        (blob) =>
          blob ? resolve(blob) : reject(new Error("Could not encode image")),
        mimeType,
        quality
      )
    )
  }

  private base64(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () =>
        resolve(
          String(reader.result).slice(String(reader.result).indexOf(",") + 1)
        )
      reader.onerror = () => reject(reader.error ?? new Error("Read failed"))
      reader.readAsDataURL(blob)
    })
  }
}
