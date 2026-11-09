import { useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"
import type { SelectedLineRange } from "@pierre/diffs"
import { toast } from "sonner"

import {
  selectionExcerpts,
  serializeExcerpts,
} from "@/features/agents/utils/codeExcerpt"
import { quoteFromPage } from "@/features/agents/components/composer/ComposerAttachments"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { commentRangeLabel } from "@/features/reviews/lib/lineRange"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

/** Attaches the selected lines to the next chat message as a card; a question sends them straight away. */
export function useAskAboutLines(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const sendInChat = useReviewPage((state) => state.sendInChat)
  const attachToChat = useReviewPage((state) => state.attachToChat)
  return useCallback(
    async (path: string, range: SelectedLineRange, question = "") => {
      // Without the file's contents, the chat still gets where the lines are.
      const byLocation = () => {
        const location = `${path}:${commentRangeLabel(range)}`
        if (question) sendInChat(`\`${location}\`\n\n${question}`)
        else
          attachToChat([
            quoteFromPage({
              label: "Ask",
              location,
              comment: `About \`${location}\`.`,
            }),
          ])
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
        const excerpts = selectionExcerpts(path, contents, range)
        if (question) sendInChat(serializeExcerpts(question, excerpts))
        else
          attachToChat(
            excerpts.map((excerpt) => ({ kind: "code" as const, excerpt }))
          )
      } catch (error) {
        console.warn("Could not load the lines for the chat", error)
        toast.error(
          "Couldn't load those lines; the chat has their location instead"
        )
        byLocation()
      }
    },
    [attachToChat, pr, queryClient, sendInChat]
  )
}
