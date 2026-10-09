import { Avatar } from "@langchain/macaw-components/Avatar"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { useQuery } from "@tanstack/react-query"
import { useMemo, useState } from "react"

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
      <PopoverTrigger asChild>
        <Button
          size="xs"
          color="secondary"
          variant="outlined"
          leftDecorator={ChatCircleIcon}
          tagText={count > 0 ? String(count) : undefined}
          aria-label="PR comments"
        >
          Comments
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-96 overflow-hidden p-0 text-primary"
      >
        <div className="border-b border-default p-space-2">
          <Input
            size="sm"
            variant="plain"
            leftIcon={MagnifyingGlassIcon}
            aria-label="Search comments"
            value={query}
            onChange={setQuery}
            placeholder="Search comments"
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
                    className="flex w-full gap-2 px-3 py-2 text-left hover:bg-elevated-hover"
                  >
                    <Avatar
                      size="xs"
                      shape="circle"
                      className="mt-0.5"
                      label={comment.author}
                      imageUrl={comment.author_avatar_url || undefined}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-xxs">
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
      </PopoverContent>
    </Popover>
  )
}
