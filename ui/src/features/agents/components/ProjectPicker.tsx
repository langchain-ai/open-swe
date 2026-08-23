import { useEffect, useState } from "react"
import { ChevronUp, Folder, GitBranch } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Sheet, SheetPopup } from "@/components/ui/sheet"
import {
  localProjectsApi,
  type DirectoryListing,
} from "@/features/agents/lib/localProjectsApi"

/**
 * Picks a directory on the machine running the server. A browser has no native
 * folder dialog, and the paths that matter belong to the server anyway, so the
 * server enumerates them and this walks the result.
 */
export function ProjectPicker({
  open,
  onClose,
  onChoose,
}: {
  open: boolean
  onClose: () => void
  onChoose: (cwd: string) => void
}) {
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    if (!open) return
    setError(null)
    setPending(true)
    localProjectsApi
      .browse()
      .then(setListing)
      .catch((cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not read that directory"
        )
      )
      .finally(() => setPending(false))
  }, [open])

  const go = (path: string) => {
    setPending(true)
    setError(null)
    localProjectsApi
      .browse(path)
      .then(setListing)
      .catch((cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not read that directory"
        )
      )
      .finally(() => setPending(false))
  }

  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetPopup side="right" className="flex w-[28rem] flex-col gap-3 p-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-medium">Choose a project</h2>
          <p className="truncate font-mono text-xs text-muted-foreground">
            {listing?.path ?? "…"}
          </p>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <ScrollArea className="min-h-0 flex-1 rounded border">
          <div className="flex flex-col p-1">
            {listing?.parent && (
              <button
                type="button"
                onClick={() => go(listing.parent!)}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
              >
                <ChevronUp className="size-4 shrink-0" />
                Up one level
              </button>
            )}
            {listing?.entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                onClick={() => go(entry.path)}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
              >
                {entry.isRepository ? (
                  <GitBranch className="size-4 shrink-0 opacity-70" />
                ) : (
                  <Folder className="size-4 shrink-0 opacity-70" />
                )}
                <span className="truncate">{entry.name}</span>
              </button>
            ))}
            {listing && !listing.entries.length && (
              <p className="px-2 py-1.5 text-sm text-muted-foreground">
                No subdirectories here.
              </p>
            )}
          </div>
        </ScrollArea>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!listing || pending}
            onClick={() => listing && onChoose(listing.path)}
          >
            Use this folder
          </Button>
        </div>
      </SheetPopup>
    </Sheet>
  )
}
