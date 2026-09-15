import { createContext, useContext, useMemo } from "react"

import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import type { ImageChunk } from "./types"

/** Resolves an offloaded image's stored file name to an `<img>` URL. */
export type ImageSourceResolver = (imageName: string) => string | null

const ImageSourceContext = createContext<ImageSourceResolver | null>(null)

/** Overrides where offloaded images are fetched from (desktop threads serve them locally). */
export const ImageSourceProvider = ImageSourceContext.Provider

const IMAGE_EXTENSIONS: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
}

/** The file name the agent stored an offloaded image under, mirroring `agent/thread_images.py`. */
export function imageFileName(fileId: string, mimeType: string): string | null {
  const extension = IMAGE_EXTENSIONS[mimeType]
  return extension ? `${fileId}.${extension}` : null
}

/** Desktop threads keep their images on disk; the app's protocol handler serves them. */
export function localImageSrc(threadId: string, imageName: string): string {
  return `/local-images/${encodeURIComponent(threadId)}/${encodeURIComponent(imageName)}`
}

function dashboardImageSrc(threadId: string, imageName: string): string {
  return dashboardApiUrl(
    `/threads/${encodeURIComponent(threadId)}/images/${encodeURIComponent(imageName)}`
  )
}

/**
 * The resolver a transcript should provide: an inherited one (desktop threads
 * serve images locally), otherwise the dashboard API for `threadId`.
 */
export function useImageSourceResolver(
  threadId?: string
): ImageSourceResolver | null {
  const inherited = useContext(ImageSourceContext)
  return useMemo(() => {
    if (inherited) return inherited
    if (!threadId) return null
    return (imageName: string) => dashboardImageSrc(threadId, imageName)
  }, [inherited, threadId])
}

/**
 * The `src` for an image chunk: inline bytes as a data URL, otherwise the
 * offloaded image by reference through the surrounding `ImageSourceProvider`.
 * `null` when nothing can serve the reference.
 */
export function useImageChunkSrc(chunk: ImageChunk): string | null {
  const resolver = useContext(ImageSourceContext)
  const { base64, fileId, mimeType } = chunk
  if (base64) return `data:${mimeType};base64,${base64}`
  const imageName = fileId ? imageFileName(fileId, mimeType) : null
  if (!imageName || !resolver) return null
  return resolver(imageName)
}
