import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChatCircleIcon, MagnifyingGlassIcon } from "@phosphor-icons/react"

import type { PrReviewComment } from "@/lib/api"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

function basename(path: string): string {
  const idx = path.lastIndexOf("/")
  return idx === -1 ? path : path.slice(idx + 1)
}

// Devin-style dropdown surfacing inline PR comments left by people (the
// reviewer's own findings already render inline + in the side panel, so they're
// filtered out). Each entry links to the comment thread on GitHub.
export function ReviewCommentsMenu({
  owner,
  repo,
  number,
  onSelect,
}: {
  owner: string
  repo: string
  number: number
  onSelect: (comment: PrReviewComment) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const wrapperRef = useRef<HTMLDivElement | null>(null)

  const comments = useQuery({
    queryKey: ["reviewComments", owner, repo, number],
    queryFn: () => api.listReviewComments(owner, repo, number),
    enabled: Number.isFinite(number),
    staleTime: 30_000,
  })

  const otherComments = useMemo(
    () => (comments.data?.comments ?? []).filter((c) => !c.is_open_swe),
    [comments.data]
  )
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return otherComments
    return otherComments.filter((c) =>
      `${c.author} ${c.path} ${c.body}`.toLowerCase().includes(q)
    )
  }, [otherComments, query])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !wrapperRef.current?.contains(event.target)
      ) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false)
    }
    window.addEventListener("pointerdown", onPointerDown)
    window.addEventListener("keydown", onKeyDown)
    return () => {
      window.removeEventListener("pointerdown", onPointerDown)
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [open])

  const count = otherComments.length

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label="PR comments"
        aria-expanded={open}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border border-default px-2 py-1 text-xs text-secondary hover:text-primary",
          open && "text-primary"
        )}
      >
        <ChatCircleIcon className="size-3.5" />
        <span>Comments</span>
        {count > 0 && (
          <span className="rounded bg-surface-level-2 px-1 text-[10px] font-medium text-primary">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute top-full right-0 z-50 mt-1 w-96 overflow-hidden rounded-md border border-default bg-elevated text-primary shadow-md">
          <div className="flex items-center gap-1.5 border-b border-default px-2 py-1.5">
            <MagnifyingGlassIcon className="size-3.5 shrink-0 text-secondary" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search comments"
              className="w-full bg-transparent text-xs outline-none placeholder:text-secondary"
            />
          </div>
          <div className="max-h-96 overflow-y-auto">
            {comments.isLoading ? (
              <p className="px-3 py-4 text-center text-xs text-secondary">
                Loading…
              </p>
            ) : comments.isError ? (
              <p className="px-3 py-4 text-center text-xs text-error-secondary">
                Failed to load comments
              </p>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-secondary">
                {otherComments.length === 0
                  ? "No comments yet"
                  : "No matching comments"}
              </p>
            ) : (
              <ul className="divide-y divide-default">
                {filtered.map((comment) => (
                  <li key={comment.id}>
                    <button
                      type="button"
                      onClick={() => {
                        onSelect(comment)
                        setOpen(false)
                      }}
                      className="flex w-full gap-2 px-3 py-2 text-left hover:bg-surface-level-2/50"
                    >
                      {comment.author_avatar_url ? (
                        <img
                          src={comment.author_avatar_url}
                          alt=""
                          className="mt-0.5 size-4 shrink-0 rounded-full"
                        />
                      ) : (
                        <span className="mt-0.5 size-4 shrink-0 rounded-full bg-surface-level-2" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 text-[11px]">
                          <span className="font-medium text-primary">
                            {comment.author}
                          </span>
                          {comment.path && (
                            <span className="truncate font-mono text-secondary">
                              {basename(comment.path)}
                              {comment.line !== null ? `:${comment.line}` : ""}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-xs text-secondary">
                          {comment.body}
                        </p>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
