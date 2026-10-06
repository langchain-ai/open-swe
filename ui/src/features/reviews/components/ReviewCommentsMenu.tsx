import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import {
  SCROLL_HOST_CLASS,
  ScrollAreaBody,
} from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import { AlertTriangle, MessageSquare, Search } from "@/components/glyphs"
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
        render={<Button size="compact" variant="outline" />}
        aria-label="PR comments"
      >
        <Icon icon={MessageSquare} size="sm" className="text-ink-subtle" />
        Comments
        {count > 0 && (
          <Badge tier="chip" className="text-ink">
            {count}
          </Badge>
        )}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        inset="flush"
        aria-label="PR comments"
        className="w-96 max-w-(--available-width)"
      >
        <Box padding="sm" className="border-b border-line">
          <SearchInput
            label="Search comments"
            placeholder="Search comments"
            value={query}
            onValueChange={setQuery}
            autoFocus
          />
        </Box>
        {comments.isLoading ? (
          <Stack gap="sm" padding="md">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </Stack>
        ) : comments.isError ? (
          <Inline
            role="alert"
            gap="sm"
            justify="center"
            padding="lg"
            className="text-label text-risk"
          >
            <Icon icon={AlertTriangle} size="sm" />
            Failed to load comments
          </Inline>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={otherComments.length === 0 ? MessageSquare : Search}
            title={
              otherComments.length === 0
                ? "No comments yet"
                : "No matching comments"
            }
          />
        ) : (
          <Box className={cn(SCROLL_HOST_CLASS, "max-h-96")}>
            <ScrollAreaBody overflow="vertical">
              <Stack render={<ul />} className="divide-y divide-line">
                {filtered.map((comment) => (
                  <li key={comment.id}>
                    <Inline
                      render={
                        <button
                          type="button"
                          onClick={() => {
                            onSelect(comment)
                            setOpen(false)
                          }}
                        />
                      }
                      gap="sm"
                      align="start"
                      className="w-full px-3 py-2 text-left hover:bg-hover"
                    >
                      <Avatar
                        name={comment.author}
                        src={comment.author_avatar_url ?? undefined}
                        size="chat"
                        className="mt-0.5"
                      />
                      <Stack gap="xs" className="min-w-0 flex-1">
                        <Inline gap="sm" className="min-w-0 text-meta">
                          <span className="font-medium text-ink">
                            {comment.author}
                          </span>
                          {comment.path && (
                            <span className="truncate font-mono text-ink-subtle">
                              {basename(comment.path)}
                              {comment.line !== null ? `:${comment.line}` : ""}
                            </span>
                          )}
                        </Inline>
                        <p className="line-clamp-2 text-meta text-ink-subtle">
                          {comment.body}
                        </p>
                      </Stack>
                    </Inline>
                  </li>
                ))}
              </Stack>
            </ScrollAreaBody>
          </Box>
        )}
      </PopoverContent>
    </Popover>
  )
}
