import { createContext, useContext, useEffect, useState } from "react"

import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import type { Client } from "@langchain/langgraph-sdk"
import type { ImageChunk } from "./types"

/** Resolves an offloaded image's `fileId` to something an `<img>` can load. */
export type ImageSourceResolver = (fileId: string) => Promise<string | null>

const ImageSourceContext = createContext<ImageSourceResolver | null>(null)

/** Overrides how offloaded images are fetched (desktop threads read the local store). */
export const ImageSourceProvider = ImageSourceContext.Provider

function dataUrl(mimeType: string, base64: string): string {
  return `data:${mimeType};base64,${base64}`
}

/** Desktop threads have no dashboard API, so read the stored item directly. */
export async function storeImageSrc(
  client: Client,
  fileId: string
): Promise<string | null> {
  const item = await client.store.getItem(["thread_images"], fileId)
  const value = item?.value as
    | { base64?: unknown; mime_type?: unknown }
    | null
    | undefined
  if (typeof value?.base64 !== "string" || typeof value.mime_type !== "string")
    return null
  return dataUrl(value.mime_type, value.base64)
}

/**
 * The `src` for an image chunk: inline bytes as a data URL, otherwise the
 * offloaded image by reference (cloud threads stream it through the dashboard
 * API). `null` while a resolver is still fetching or when nothing can serve
 * the reference.
 */
export function useImageChunkSrc(
  chunk: ImageChunk,
  threadId?: string
): string | null {
  const resolver = useContext(ImageSourceContext)
  const { base64, fileId, mimeType } = chunk
  const direct = base64
    ? dataUrl(mimeType, base64)
    : fileId && !resolver && threadId
      ? dashboardApiUrl(
          `/threads/${encodeURIComponent(threadId)}/images/${encodeURIComponent(fileId)}`
        )
      : null
  const [resolved, setResolved] = useState<string | null>(null)

  useEffect(() => {
    if (direct || !fileId || !resolver) return
    let active = true
    resolver(fileId)
      .then((src) => {
        if (active) setResolved(src)
      })
      .catch(() => {
        if (active) setResolved(null)
      })
    return () => {
      active = false
    }
  }, [direct, fileId, resolver])

  return direct ?? resolved
}
