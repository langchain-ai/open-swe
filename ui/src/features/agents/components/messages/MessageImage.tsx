import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useEffect, useState } from "react"

import { cn } from "@/lib/utils"
import { fetchImageBlob } from "@/features/agents/lib/transcript/api"
import type { AnyImageChunk } from "@/features/agents/lib/types"

/**
 * The `src` for an image chunk. Inline bytes become a data URL and a remote
 * reference is loaded by the browser, but our own attachments need the session
 * cookie, which only a `fetch` carries reliably on a split deployment — so
 * those are fetched once and shown through a blob URL that is revoked with the
 * component.
 */
function useImageSource(chunk: AnyImageChunk): {
  src: string | null
  failed: boolean
} {
  const sessionUrl =
    "url" in chunk && chunk.credentials === "session" ? chunk.url : null
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!sessionUrl) return
    let disposed = false
    let objectUrl: string | null = null
    void fetchImageBlob(sessionUrl).then(
      (blob) => {
        if (disposed) return
        objectUrl = URL.createObjectURL(blob)
        setBlobUrl(objectUrl)
      },
      () => {
        if (!disposed) setFailed(true)
      }
    )
    return () => {
      disposed = true
      setBlobUrl(null)
      setFailed(false)
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [sessionUrl])

  if ("base64" in chunk)
    return { src: `data:${chunk.mimeType};base64,${chunk.base64}`, failed }
  if (sessionUrl) return { src: blobUrl, failed }
  return { src: chunk.url, failed }
}

/** One attachment from a message: an image, or a named chip for any other file. */
export function MessageImage({
  chunk,
  className,
  fill = false,
}: {
  chunk: AnyImageChunk
  className?: string
  fill?: boolean
}) {
  if (!chunk.mimeType || chunk.mimeType.startsWith("image/"))
    return (
      <MessageImageContent chunk={chunk} className={className} fill={fill} />
    )
  return <AttachedFile name={chunk.fileName || "file"} />
}

/** A non-image attachment, shown by name. */
export function AttachedFile({ name }: { name: string }) {
  return (
    <div className="flex h-16 max-w-48 items-center gap-space-2 rounded-lg border border-default bg-surface-level-2 px-space-3 text-xs text-secondary">
      <FileTextIcon className="size-4 shrink-0 text-icon-secondary" />
      <span className="truncate">{name}</span>
    </div>
  )
}

function MessageImageContent({
  chunk,
  className,
  fill,
}: {
  chunk: AnyImageChunk
  className?: string
  fill: boolean
}) {
  const { src, failed } = useImageSource(chunk)
  const [expanded, setExpanded] = useState(false)
  const label = chunk.fileName || "image"

  if (failed || !src)
    return (
      <div
        className={cn(className, "h-32 w-48 max-w-full bg-surface-level-2")}
        data-testid={failed ? "message-image-error" : "message-image-loading"}
        role="img"
        aria-label={
          failed ? `${label} could not be loaded` : `Loading ${label}`
        }
      >
        {!failed && <Skeleton className="size-full" />}
      </div>
    )
  return (
    <>
      <button
        type="button"
        aria-label={`Expand ${label}`}
        onClick={() => setExpanded(true)}
        className={cn(
          "block cursor-zoom-in border-0 bg-transparent p-0 text-left",
          fill ? "w-full" : "w-fit max-w-full"
        )}
      >
        <img
          src={src}
          alt={label}
          className={cn(className, "duration-slow animate-in fade-in")}
        />
      </button>
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent
          className="h-[calc(100dvh-2rem)] w-[95vw] max-w-[95vw]"
          childrenClassName="flex-1 items-center justify-center"
        >
          <DialogTitle className="sr-only">{label}</DialogTitle>
          <img
            src={src}
            alt={label}
            className="h-full min-h-0 w-full object-contain"
          />
        </DialogContent>
      </Dialog>
    </>
  )
}
