import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef, useState } from "react"

import { api } from "@/lib/api"
import { copyText } from "@/features/reviews/lib/copyText"
import { RecentReviewPagesMenu } from "./RecentReviewPagesMenu"
import { reviewQueries, type PullRequestRef } from "./queries"

export function PullRequestTitle({ pr }: { pr: PullRequestRef }) {
  const query = reviewQueries.detail(pr)
  const detail = useQuery(query).data
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const editButton = useRef<HTMLButtonElement>(null)
  const close = () => {
    setDraft(null)
    requestAnimationFrame(() => editButton.current?.focus())
  }
  const update = useMutation({
    mutationFn: (title: string) =>
      api.updatePullRequestTitle(pr.owner, pr.repo, pr.number, title),
    onMutate: async (title) => {
      await queryClient.cancelQueries(query)
      const previous = queryClient.getQueryData(query.queryKey)
      queryClient.setQueryData(query.queryKey, (current) =>
        current ? { ...current, pr: { ...current.pr, title } } : current
      )
      close()
      return previous
    },
    meta: { errorTitle: "Couldn't update title" },
    onError: (_error, title, previous) => {
      queryClient.setQueryData(query.queryKey, previous)
      setDraft(title)
    },
    onSettled: () => queryClient.invalidateQueries(query),
  })
  const title = draft?.trim() ?? ""
  const canSave =
    title.length > 0 && title !== detail?.pr.title && !update.isPending

  if (draft !== null)
    return (
      <form
        aria-label="Edit PR title"
        className="flex min-w-0 flex-1 flex-wrap items-center gap-space-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (canSave) update.mutate(title)
        }}
        onKeyDown={(event) => {
          event.stopPropagation()
          if (event.nativeEvent.isComposing) {
            if (event.key === "Enter") event.preventDefault()
            return
          }
          if (event.key === "Escape") {
            event.preventDefault()
            close()
          }
        }}
      >
        <Input
          size="md"
          aria-label="PR title"
          className="min-w-0 flex-1"
          autoFocus
          maxLength={256}
          debounceMs={0}
          value={draft}
          onChange={setDraft}
        />
        <Button type="submit" size="sm" disabled={!canSave}>
          Save title
        </Button>
        <Button
          type="button"
          size="sm"
          color="secondary"
          variant="plain"
          onClick={close}
        >
          Cancel
        </Button>
      </form>
    )

  return (
    <>
      <h2 className="flex min-w-0 items-center">
        <RecentReviewPagesMenu pr={pr}>
          {detail?.pr.title ?? `${pr.owner}/${pr.repo}`}
        </RecentReviewPagesMenu>
      </h2>
      <Button
        type="button"
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={`Copy PR number #${pr.number}`}
        title="Copy PR number"
        className="h-auto shrink-0 border-0 px-space-1 py-0 font-normal tabular-nums"
        onClick={() => copyText(`#${pr.number}`, "Copied the PR number")}
      >
        #{pr.number}
      </Button>
      {detail && (
        <IconButton
          ref={editButton}
          icon={PencilSimpleIcon}
          label="Edit title"
          size="sm"
          color="secondary"
          variant="plain"
          disabled={update.isPending}
          className="disabled:bg-transparent"
          onClick={() => setDraft(detail.pr.title)}
        />
      )}
    </>
  )
}
