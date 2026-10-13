import { toast } from "sonner"
import { FileIcon } from "@phosphor-icons/react/dist/ssr/File"
import { useCallback } from "react"

import { cn } from "@/lib/utils"
import { fetchAttachmentBlob } from "@/features/agents/lib/transcript/api"
import type { FileChunk, RemoteFileChunk } from "@/features/agents/lib/types"

function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = fileName
  link.click()
  URL.revokeObjectURL(url)
}

/** A non-image attachment: its name and sandbox path, downloadable on click. */
export function MessageFile({
  chunk,
  className,
}: {
  chunk: FileChunk | RemoteFileChunk
  className?: string
}) {
  const label = chunk.fileName || "file"
  const path = "path" in chunk ? chunk.path : undefined

  const download = useCallback(async () => {
    if ("base64" in chunk) {
      const bytes = Uint8Array.from(atob(chunk.base64), (c) => c.charCodeAt(0))
      saveBlob(new Blob([bytes], { type: chunk.mimeType }), label)
      return
    }
    if (chunk.credentials === "none") {
      window.open(chunk.url, "_blank", "noopener,noreferrer")
      return
    }
    try {
      saveBlob(await fetchAttachmentBlob(chunk.url), label)
    } catch {
      toast.error(`Couldn't download ${label}`)
    }
  }, [chunk, label])

  return (
    <button
      type="button"
      onClick={() => void download()}
      title={path ? `${label} · ${path}` : label}
      data-testid="message-file"
      className={cn(
        "flex max-w-full min-w-0 items-center gap-space-2 rounded-lg border border-default bg-surface-level-1 px-space-2 py-space-1 text-left transition-colors duration-normal hover:bg-surface-level-3 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
        className
      )}
    >
      <FileIcon size={16} className="shrink-0 text-secondary" aria-hidden />
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-xs font-medium text-primary">
          {label}
        </span>
        {path && (
          <span className="truncate font-mono text-xxs text-tertiary">
            {path}
          </span>
        )}
      </span>
    </button>
  )
}
