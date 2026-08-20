import { useState } from "react"
import { ExternalLink, Globe2, RefreshCw } from "lucide-react"

export function normalizePreviewUrl(value: string): string | null {
  const candidate = value.trim()
  if (!candidate) return null
  try {
    const url = new URL(
      candidate.includes("://") ? candidate : `http://${candidate}`
    )
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null
  } catch {
    return null
  }
}

export function BrowserPanel({
  openExternal,
}: {
  openExternal: (url: string) => void
}) {
  const [input, setInput] = useState("http://localhost:3000")
  const [url, setUrl] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const navigate = () => {
    const next = normalizePreviewUrl(input)
    if (next) {
      setInput(next)
      setUrl(next)
    }
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <form
        className="flex h-10 shrink-0 items-center gap-1 border-b border-border px-2"
        onSubmit={(event) => {
          event.preventDefault()
          navigate()
        }}
      >
        <Globe2 className="size-3.5 shrink-0 text-muted-foreground" />
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          aria-label="Preview address"
          className="h-7 min-w-0 flex-1 rounded-md bg-muted px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
        />
        <button
          type="button"
          aria-label="Reload preview"
          className="rounded p-1.5 text-muted-foreground hover:bg-accent"
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw className="size-3.5" />
        </button>
        <button
          type="button"
          aria-label="Open in browser"
          disabled={!url}
          className="rounded p-1.5 text-muted-foreground hover:bg-accent disabled:opacity-40"
          onClick={() => url && openExternal(url)}
        >
          <ExternalLink className="size-3.5" />
        </button>
      </form>
      {url ? (
        <iframe
          key={`${url}:${revision}`}
          title="Browser preview"
          src={url}
          sandbox="allow-forms allow-modals allow-popups allow-scripts"
          className="min-h-0 flex-1 border-0 bg-white"
        />
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center text-xs text-muted-foreground">
          <Globe2 className="size-8 opacity-40" />
          Enter an HTTP or HTTPS address to open a preview.
        </div>
      )}
    </div>
  )
}
