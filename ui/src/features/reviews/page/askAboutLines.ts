import { useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"
import type { SelectedLineRange } from "@pierre/diffs"
import { toast } from "sonner"

import { selectionExcerpts } from "@/features/agents/utils/codeExcerpt"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { commentRangeLabel } from "@/features/reviews/lib/lineRange"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

/** Attaches the selected lines to the next chat message; a question sends it straight away. */
export function useAskAboutLines(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const askInChat = useReviewPage((state) => state.askInChat)
  const sendInChat = useReviewPage((state) => state.sendInChat)
  const focusChat = useReviewPage((state) => state.focusChat)
  const attachToChat = useReviewPage((state) => state.attachToChat)
  return useCallback(
    async (path: string, range: SelectedLineRange, question = "") => {
      // Without the file's contents, the chat still gets where the lines are.
      const byLocation = () => {
        const text = [`\`${path}:${commentRangeLabel(range)}\``, question]
          .filter(Boolean)
          .join("\n\n")
        if (question) sendInChat(text)
        else askInChat(text)
      }
      const file = queryClient
        .getQueryData(reviewQueries.diff(pr).queryKey)
        ?.files.find((candidate) => candidate.path === path)
      if (!file) return byLocation()
      try {
        const contents = await loadReviewFileContents(
          pr.owner,
          pr.repo,
          pr.number,
          file
        )
        attachToChat(selectionExcerpts(path, contents, range))
        if (question) sendInChat(question)
        else focusChat()
      } catch (error) {
        console.warn("Could not load the lines for the chat", error)
        toast.error(
          "Couldn't load those lines; the chat has their location instead"
        )
        byLocation()
      }
    },
    [askInChat, attachToChat, focusChat, pr, queryClient, sendInChat]
  )
}
