import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChatCircleIcon, MagnifyingGlassIcon } from "@phosphor-icons/react"

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription } from "@/components/ui/empty"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import type { PrReviewComment } from "@/lib/api"
import { api } from "@/lib/api"

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

  const count = otherComments.length

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            aria-label="PR comments"
            className="text-muted-foreground"
          />
        }
      >
        <ChatCircleIcon />
        <span>Comments</span>
        {count > 0 && (
          <span className="rounded bg-muted px-1 text-[10px] font-medium text-foreground">
            {count}
          </span>
        )}
      </PopoverTrigger>
      <PopoverPopup
        align="end"
        className="flex w-96 flex-col overflow-hidden p-0"
      >
        <div className="border-b border-border p-1.5">
          <InputGroup>
            <InputGroupAddon>
              <MagnifyingGlassIcon />
            </InputGroupAddon>
            <InputGroupInput
              aria-label="Search comments"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search comments"
            />
          </InputGroup>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {comments.isLoading ? (
            <Empty className="p-4">
              <Spinner />
            </Empty>
          ) : comments.isError ? (
            <p className="px-3 py-4 text-center text-xs text-destructive">
              Failed to load comments
            </p>
          ) : filtered.length === 0 ? (
            <Empty className="p-4">
              <EmptyDescription>
                {otherComments.length === 0
                  ? "No comments yet"
                  : "No matching comments"}
              </EmptyDescription>
            </Empty>
          ) : (
            <ul className="divide-y divide-border">
              {filtered.map((comment) => (
                <li key={comment.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(comment)
                      setOpen(false)
                    }}
                    className="flex w-full gap-2 px-3 py-2 text-left hover:bg-muted/50"
                  >
                    <Avatar className="mt-0.5 size-4">
                      {comment.author_avatar_url && (
                        <AvatarImage src={comment.author_avatar_url} alt="" />
                      )}
                      <AvatarFallback />
                    </Avatar>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-[11px]">
                        <span className="font-medium text-foreground">
                          {comment.author}
                        </span>
                        {comment.path && (
                          <span className="truncate font-mono text-muted-foreground">
                            {basename(comment.path)}
                            {comment.line !== null ? `:${comment.line}` : ""}
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                        {comment.body}
                      </p>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverPopup>
    </Popover>
  )
}
