import { useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"
import type { SelectedLineRange } from "@pierre/diffs"
import { toast } from "sonner"

import {
  selectionExcerpts,
  type CodeExcerpt,
} from "@/features/agents/utils/codeExcerpt"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { commentRangeLabel } from "@/features/reviews/lib/lineRange"
import {
  reviewSessionKey,
  useReviewSessions,
} from "@/features/reviews/lib/reviewSession"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

/** The selected lines with their context, or null when the file isn't in the diff. */
function useLineExcerpts(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  return useCallback(
    async (
      path: string,
      range: SelectedLineRange
    ): Promise<Array<CodeExcerpt> | null> => {
      const file = queryClient
        .getQueryData(reviewQueries.diff(pr).queryKey)
        ?.files.find((candidate) => candidate.path === path)
      if (!file) return null
      const contents = await loadReviewFileContents(
        pr.owner,
        pr.repo,
        pr.number,
        file
      )
      return selectionExcerpts(path, contents, range)
    },
    [pr, queryClient]
  )
}

/** Attaches the selected lines to the next chat message; a question sends it straight away. */
export function useAskAboutLines(pr: PullRequestRef) {
  const excerptsFor = useLineExcerpts(pr)
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
      try {
        const excerpts = await excerptsFor(path, range)
        if (!excerpts) return byLocation()
        attachToChat(excerpts)
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
    [askInChat, attachToChat, excerptsFor, focusChat, sendInChat]
  )
}

/**
 * Saves a comment to the review session's drafts at once, sending nothing;
 * the code it's about follows as soon as the file loads.
 */
export function useDraftAgentComment(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const excerptsFor = useLineExcerpts(pr)
  const add = useReviewSessions((state) => state.add)
  const attachExcerpts = useReviewSessions((state) => state.attachExcerpts)
  return useCallback(
    (path: string, range: SelectedLineRange, body: string) => {
      const key = reviewSessionKey(pr)
      const id = crypto.randomUUID()
      add(key, {
        id,
        path,
        range,
        excerpts: [],
        headSha:
          queryClient.getQueryData(reviewQueries.detail(pr).queryKey)
            ?.head_sha ?? null,
        body,
        createdAt: Date.now(),
      })
      excerptsFor(path, range).then(
        (excerpts) => {
          if (excerpts) attachExcerpts(key, id, excerpts)
        },
        (error: unknown) => {
          console.warn("Could not load the lines for a draft", error)
          toast.error(
            "Couldn't load those lines; the draft keeps their location instead"
          )
        }
      )
    },
    [add, attachExcerpts, excerptsFor, pr, queryClient]
  )
}
