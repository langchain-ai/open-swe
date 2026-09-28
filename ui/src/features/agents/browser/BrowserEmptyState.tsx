import { Globe2, History, X } from "lucide-react"

import type { BrowserHistoryEntry } from "@/features/agents/browser/browserHistoryStore"

interface Props {
  entries: ReadonlyArray<BrowserHistoryEntry>
  hint: string
  onOpenUrl: (url: string) => void
  onRemove: (url: string) => void
}

function entryLabel(url: string): string {
  try {
    const parsed = new URL(url)
    const path = parsed.pathname === "/" ? "" : parsed.pathname
    return `${parsed.host}${path}${parsed.search}${parsed.hash}`
  } catch {
    return url
  }
}

/** Shown on a tab with no page: recently visited URLs for this project. */
export function BrowserEmptyState({
  entries,
  hint,
  onOpenUrl,
  onRemove,
}: Props) {
  const recents = entries.slice(0, 8)
  if (recents.length === 0) {
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 px-8 text-center">
        <span className="flex size-9 items-center justify-center rounded-lg border border-border/80 bg-card">
          <Globe2 className="size-4 text-muted-foreground" />
        </span>
        <div>
          <h3 className="text-sm font-medium text-foreground">No page yet</h3>
          <p className="mt-1 max-w-xs text-xs leading-relaxed text-muted-foreground">
            {hint}
          </p>
        </div>
      </div>
    )
  }
  return (
    <div className="flex h-full min-h-0 overflow-y-auto px-5 py-8">
      <div className="mx-auto flex w-full max-w-xl flex-col gap-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <History className="size-4 shrink-0" />
          <h2 className="font-medium">Recently visited</h2>
        </div>
        <div className="divide-y divide-border/60 overflow-hidden rounded-lg border border-border/80 bg-card">
          {recents.map((entry) => {
            const label = entryLabel(entry.url)
            return (
              <div
                key={entry.url}
                className="group relative flex w-full items-center"
              >
                <button
                  type="button"
                  onClick={() => onOpenUrl(entry.url)}
                  className="flex w-full items-center gap-3 px-3 py-2.5 pr-10 text-left hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset"
                >
                  <Globe2 className="size-4 shrink-0 text-muted-foreground" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm font-medium text-foreground">
                      {entry.title ?? label}
                    </span>
                    {entry.title ? (
                      <span className="truncate text-xs text-muted-foreground">
                        {label}
                      </span>
                    ) : null}
                  </div>
                </button>
                <button
                  type="button"
                  aria-label={`Remove ${label} from history`}
                  onClick={() => onRemove(entry.url)}
                  className="absolute right-3 rounded p-1 text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-accent hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            )
          })}
        </div>
        <p className="px-1 text-xs text-muted-foreground">{hint}</p>
      </div>
    </div>
  )
}
