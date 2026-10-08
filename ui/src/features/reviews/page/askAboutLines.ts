import { useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"
import type { SelectedLineRange } from "@pierre/diffs"
import { toast } from "sonner"

import { selectionExcerpts } from "@/features/agents/utils/codeExcerpt"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { commentRangeLabel } from "@/features/reviews/lib/lineRange"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

/** Attaches the selected lines to the next chat message, with an optional question in the composer. */
export function useAskAboutLines(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const askInChat = useReviewPage((state) => state.askInChat)
  const attachToChat = useReviewPage((state) => state.attachToChat)
  return useCallback(
    async (path: string, range: SelectedLineRange, question = "") => {
      const file = queryClient
        .getQueryData(reviewQueries.diff(pr).queryKey)
        ?.files.find((candidate) => candidate.path === path)
      const label = `\`${path}:${commentRangeLabel(range)}\``
      if (!file) {
        askInChat(`${label}\n\n${question}`)
        return
      }
      try {
        const contents = await loadReviewFileContents(
          pr.owner,
          pr.repo,
          pr.number,
          file
        )
        attachToChat(selectionExcerpts(path, contents, range), question)
      } catch (error) {
        console.warn("Could not load the lines for the chat", error)
        toast.error(
          "Couldn't load those lines; the chat has their location instead"
        )
        askInChat(`${label}\n\n${question}`)
      }
    },
    [askInChat, attachToChat, pr, queryClient]
  )
}
