import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useEffect, useState } from "react"

import { cn } from "@/lib/utils"
import { fetchAttachmentBlob } from "@/features/agents/lib/transcript/api"
import type { AnyMediaChunk } from "@/features/agents/lib/types"

/** Resolve inline, public, or session-authenticated media to a browser source. */
function useMediaSource(chunk: AnyMediaChunk): {
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
    void fetchAttachmentBlob(sessionUrl).then(
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

/** One media attachment from a message. */
export function MessageMedia({
  chunk,
  className,
}: {
  chunk: AnyMediaChunk
  className?: string
}) {
  const { src, failed } = useMediaSource(chunk)
  const label = chunk.fileName || chunk.kind
  const [failedSource, setFailedSource] = useState<string | null>(null)
  const previewFailed = failed || (src !== null && failedSource === src)

  if (previewFailed || !src)
    return (
      <div
        className={cn(className, "h-32 w-48 max-w-full bg-surface-level-2")}
        data-testid={
          previewFailed
            ? `message-${chunk.kind}-error`
            : `message-${chunk.kind}-loading`
        }
        role={chunk.kind === "video" ? "group" : "img"}
        aria-label={
          previewFailed ? `${label} could not be loaded` : `Loading ${label}`
        }
      >
        {!previewFailed && <Skeleton className="size-full" />}
        {previewFailed && chunk.kind === "video" && src && (
          <div className="p-space-2 text-xs text-secondary">
            Video preview unavailable.{" "}
            <a className="underline" href={src} download={chunk.fileName}>
              Download video
            </a>
          </div>
        )}
      </div>
    )
  if (chunk.kind === "video")
    return (
      <video
        src={src}
        aria-label={label}
        controls
        preload="metadata"
        onError={() => setFailedSource(src)}
        className={className}
      >
        <a href={src} download={chunk.fileName}>
          Download {label}
        </a>
      </video>
    )
  return (
    <img
      src={src}
      alt={label}
      className={cn(className, "duration-slow animate-in fade-in")}
    />
  )
}
