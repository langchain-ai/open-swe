import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { ArrowBendUpLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowBendUpLeft"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { CircleIcon } from "@phosphor-icons/react/dist/ssr/Circle"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { createContext, useContext, useEffect, useState } from "react"
import type { PointerEvent as ReactPointerEvent } from "react"
import { toast } from "sonner"

import { cn } from "@/lib/utils"
import type { ImageChunk } from "@/features/agents/lib/types"
import {
  ImageMarkup,
  MARKUP_INK,
  type MarkupShape,
  type MarkupTool,
} from "./imageMarkup"

/** A marked-up copy of an image and what the reader said about it. */
export interface ImageAnnotation {
  image: ImageChunk
  comment: string
}

/**
 * Where a marked-up image goes, usually the nearest chat composer. Without a
 * provider the lightbox only enlarges.
 */
export const ImageAnnotationContext = createContext<
  ((annotation: ImageAnnotation) => void) | null
>(null)

interface ExpandableImageProps {
  src: string
  alt: string
  className?: string
  loading?: "lazy" | "eager"
  /** Off for images that are already the reader's own, like a draft's attachments. */
  annotatable?: boolean
}

/** An image that opens full size, and can be marked up into a reply, on click. */
export function ExpandableImage({
  src,
  alt,
  className,
  loading,
  annotatable = true,
}: ExpandableImageProps) {
  const [open, setOpen] = useState(false)
  const annotate = useContext(ImageAnnotationContext)
  return (
    <>
      <img
        src={src}
        alt={alt}
        loading={loading}
        role="button"
        tabIndex={0}
        aria-label={`Enlarge ${alt || "image"}`}
        className={cn(
          "cursor-zoom-in focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
          className
        )}
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          setOpen(true)
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault()
            event.stopPropagation()
            setOpen(true)
          }
        }}
      />
      {open && (
        <ImageLightbox
          src={src}
          alt={alt}
          onClose={() => setOpen(false)}
          onAnnotate={annotatable ? annotate : null}
        />
      )}
    </>
  )
}

function ImageLightbox({
  src,
  alt,
  onClose,
  onAnnotate,
}: {
  src: string
  alt: string
  onClose: () => void
  onAnnotate: ((annotation: ImageAnnotation) => void) | null
}) {
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent
        title={alt || "Image"}
        className="h-[calc(100dvh-2rem)] w-[95vw] max-w-[95vw]"
        childrenClassName="flex min-h-0 flex-1 flex-col gap-space-2"
      >
        {onAnnotate ? (
          <ImageAnnotator
            src={src}
            alt={alt}
            onSubmit={(annotation) => {
              onAnnotate(annotation)
              onClose()
            }}
          />
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center">
            <img
              src={src}
              alt={alt}
              className="max-h-full max-w-full object-contain"
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Fits a `width`×`height` image inside the element, never past its own size. */
function useFittedSize(width: number, height: number) {
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  const [box, setBox] = useState<{ width: number; height: number } | null>(null)
  useEffect(() => {
    if (!node) return
    const measure = () =>
      setBox({ width: node.clientWidth, height: node.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])
  const scale =
    box && width && height
      ? Math.min(box.width / width, box.height / height, 1)
      : 0
  return { measure: setNode, width: width * scale, height: height * scale }
}

const TOOLS: Array<{
  tool: MarkupTool
  label: string
  icon: typeof CircleIcon
}> = [
  { tool: "ellipse", label: "Circle", icon: CircleIcon },
  { tool: "arrow", label: "Arrow", icon: ArrowUpRightIcon },
]

function ImageAnnotator({
  src,
  alt,
  onSubmit,
}: {
  src: string
  alt: string
  onSubmit: (annotation: ImageAnnotation) => void
}) {
  const [markup, setMarkup] = useState(new ImageMarkup(0, 0))
  const [tool, setTool] = useState<MarkupTool>("ellipse")
  const [draft, setDraft] = useState<MarkupShape | null>(null)
  const [comment, setComment] = useState("")
  const [sending, setSending] = useState(false)
  const {
    measure,
    width: fittedWidth,
    height: fittedHeight,
  } = useFittedSize(markup.width, markup.height)

  const pointAt = (event: ReactPointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return {
      x: ((event.clientX - rect.left) / rect.width) * markup.width,
      y: ((event.clientY - rect.top) / rect.height) * markup.height,
    }
  }

  const submit = async () => {
    setSending(true)
    try {
      const response = await fetch(src, { credentials: "include" })
      if (!response.ok)
        throw new Error(`Image fetch failed: ${response.status}`)
      const image = await markup.render(await response.blob(), alt || "image")
      onSubmit({ image, comment: comment.trim() })
    } catch (error) {
      console.warn("Could not annotate image", error)
      toast.error("Couldn't attach this image to a reply")
      setSending(false)
    }
  }

  const shapes = draft ? [...markup.shapes, draft] : markup.shapes
  return (
    <>
      <div
        role="toolbar"
        aria-label="Markup tools"
        className="flex items-center gap-space-1"
      >
        {TOOLS.map((item) => (
          <IconButton
            key={item.tool}
            icon={item.icon}
            label={item.label}
            size="sm"
            color="secondary"
            variant="plain"
            aria-pressed={tool === item.tool}
            onClick={() => setTool(item.tool)}
            className={cn(tool === item.tool && "bg-selected text-primary")}
          />
        ))}
        <IconButton
          icon={ArrowCounterClockwiseIcon}
          label="Undo"
          size="sm"
          color="secondary"
          variant="plain"
          disabled={markup.shapes.length === 0}
          onClick={() => setMarkup(markup.undo())}
        />
        <IconButton
          icon={TrashIcon}
          label="Clear markup"
          size="sm"
          color="secondary"
          variant="plain"
          disabled={markup.shapes.length === 0}
          onClick={() => setMarkup(markup.clear())}
        />
        <span className="ml-space-2 text-xs text-tertiary">
          Drag on the image to circle or point at something
        </span>
      </div>
      <div
        ref={measure}
        className="flex min-h-0 flex-1 items-center justify-center overflow-hidden"
      >
        <div
          className="relative shrink-0"
          style={
            fittedWidth
              ? { width: fittedWidth, height: fittedHeight }
              : undefined
          }
        >
          <img
            src={src}
            alt={alt}
            draggable={false}
            className={cn(
              "block size-full select-none",
              !fittedWidth && "invisible"
            )}
            onLoad={(event) =>
              setMarkup(
                new ImageMarkup(
                  event.currentTarget.naturalWidth,
                  event.currentTarget.naturalHeight
                )
              )
            }
          />
          {markup.width > 0 && (
            <svg
              aria-label="Markup canvas"
              role="img"
              viewBox={`0 0 ${markup.width} ${markup.height}`}
              preserveAspectRatio="none"
              className="absolute inset-0 size-full cursor-crosshair touch-none"
              fill="none"
              stroke={MARKUP_INK}
              strokeWidth={markup.stroke}
              strokeLinecap="round"
              strokeLinejoin="round"
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId)
                const { x, y } = pointAt(event)
                setDraft({ tool, x1: x, y1: y, x2: x, y2: y })
              }}
              onPointerMove={(event) => {
                if (!draft) return
                const { x, y } = pointAt(event)
                setDraft({ ...draft, x2: x, y2: y })
              }}
              onPointerUp={() => {
                if (draft && markup.isDeliberate(draft))
                  setMarkup(markup.with(draft))
                setDraft(null)
              }}
              onPointerCancel={() => setDraft(null)}
            >
              {shapes.map((shape, index) =>
                shape.tool === "ellipse" ? (
                  <ellipse
                    key={index}
                    cx={(shape.x1 + shape.x2) / 2}
                    cy={(shape.y1 + shape.y2) / 2}
                    rx={Math.abs(shape.x2 - shape.x1) / 2}
                    ry={Math.abs(shape.y2 - shape.y1) / 2}
                  />
                ) : (
                  <path key={index} d={markup.arrowPath(shape)} />
                )
              )}
            </svg>
          )}
        </div>
      </div>
      <form
        aria-label="Reply with this image"
        className="flex items-center gap-space-2"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <Input
          size="sm"
          debounceMs={0}
          aria-label="Your comment"
          placeholder="Say what you marked up"
          value={comment}
          onChange={setComment}
          className="min-w-0 flex-1"
        />
        <Button
          type="submit"
          size="sm"
          color="primary"
          leftDecorator={ArrowBendUpLeftIcon}
          loading={sending}
          disabled={markup.width === 0}
        >
          Add to reply
        </Button>
      </form>
    </>
  )
}
