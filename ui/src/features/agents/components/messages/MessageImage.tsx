import { useEffect, useState } from "react"

import { fetchImageBlob } from "@/features/agents/lib/transcript/api"
import type { AnyImageChunk } from "@/features/agents/lib/types"

/**
 * The `src` for an image chunk. Inline bytes become a data URL and a remote
 * reference is loaded by the browser, but our own attachments need the session
 * cookie, which only a `fetch` carries reliably on a split deployment — so
 * those are fetched once and shown through a blob URL that is revoked with the
 * component.
 */
export function useImageSource(chunk: AnyImageChunk): {
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

/** One image from a message, whatever form the transcript recorded it in. */
export function MessageImage({
  chunk,
  className,
}: {
  chunk: AnyImageChunk
  className?: string
}) {
  const { src, failed } = useImageSource(chunk)
  const label = chunk.fileName || "image"

  if (failed || !src)
    return (
      <div
        className={className}
        data-testid={failed ? "message-image-error" : "message-image-loading"}
        role="img"
        aria-label={
          failed ? `${label} could not be loaded` : `Loading ${label}`
        }
      />
    )
  return <img src={src} alt={label} className={className} />
}
