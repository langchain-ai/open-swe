import { useMutation, useQueryClient } from "@tanstack/react-query"

import { useSession } from "@/lib/session"
import {
  replyToReviewThread,
  setReviewThreadResolved,
  type Conversation,
  type ReviewThread,
  type ThreadComment,
} from "@/features/reviews/lib/conversationApi"
import { reviewQueries, type PullRequestRef } from "./queries"

function patchThread(
  conversation: Conversation | undefined,
  threadId: number,
  change: (thread: ReviewThread) => ReviewThread
): Conversation | undefined {
  if (!conversation) return conversation
  return {
    ...conversation,
    threads: conversation.threads.map((thread) =>
      thread.id === threadId ? change(thread) : thread
    ),
  }
}

/** Reply to and resolve one inline thread on GitHub, updating the page before GitHub answers. */
export function useThreadActions(pr: PullRequestRef, thread: ReviewThread) {
  const queryClient = useQueryClient()
  const session = useSession()
  const key = reviewQueries.conversation(pr).queryKey

  const snapshot = async () => {
    await queryClient.cancelQueries({ queryKey: key })
    return queryClient.getQueryData<Conversation>(key)
  }
  const restore = (previous: Conversation | undefined) =>
    queryClient.setQueryData(key, previous)

  const reply = useMutation({
    mutationFn: (body: string) =>
      replyToReviewThread(pr.owner, pr.repo, pr.number, thread.id, body),
    meta: { errorTitle: "Couldn't post the reply" },
    onMutate: async (body) => {
      const previous = await snapshot()
      const optimistic: ThreadComment = {
        id: -Date.now(),
        review_id: null,
        author: session.data
          ? {
              login: session.data.login,
              avatar_url: `https://github.com/${session.data.login}.png?size=40`,
              bot: false,
              posted_by: null,
            }
          : null,
        created_at: new Date().toISOString(),
        body,
        html_url: "",
      }
      queryClient.setQueryData<Conversation | undefined>(key, (old) =>
        patchThread(old, thread.id, (t) => ({
          ...t,
          comments: [...t.comments, optimistic],
        }))
      )
      return { previous, optimisticId: optimistic.id }
    },
    onError: (_error, _body, context) => restore(context?.previous),
    onSuccess: (created, _body, context) =>
      queryClient.setQueryData<Conversation | undefined>(key, (old) =>
        patchThread(old, thread.id, (t) => ({
          ...t,
          comments: t.comments.map((c) =>
            c.id === context?.optimisticId ? created : c
          ),
        }))
      ),
  })

  const resolve = useMutation({
    mutationFn: (resolved: boolean) => {
      if (!thread.node_id) throw new Error("GitHub didn't name this thread")
      return setReviewThreadResolved(
        pr.owner,
        pr.repo,
        pr.number,
        thread.node_id,
        resolved
      )
    },
    meta: { errorTitle: "Couldn't update the conversation" },
    onMutate: async (resolved) => {
      const previous = await snapshot()
      queryClient.setQueryData<Conversation | undefined>(key, (old) =>
        patchThread(old, thread.id, (t) => ({ ...t, resolved }))
      )
      return { previous }
    },
    onError: (_error, _resolved, context) => restore(context?.previous),
    onSettled: () =>
      void queryClient.invalidateQueries({ queryKey: ["review-page-pr"] }),
  })

  return { reply, resolve }
}

/** Resolve several threads at once, e.g. every outdated one the author has already answered. */
export function useResolveThreads(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const key = reviewQueries.conversation(pr).queryKey
  return useMutation({
    mutationFn: async (threads: ReadonlyArray<ReviewThread>) => {
      const results = await Promise.allSettled(
        threads.flatMap((thread) =>
          thread.node_id
            ? [
                setReviewThreadResolved(
                  pr.owner,
                  pr.repo,
                  pr.number,
                  thread.node_id,
                  true
                ),
              ]
            : []
        )
      )
      const failed = results.filter((result) => result.status === "rejected")
      if (failed.length)
        throw new Error(
          `${failed.length} of ${results.length} conversations stayed open`
        )
    },
    meta: { errorTitle: "Couldn't resolve every conversation" },
    onMutate: async (threads) => {
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<Conversation>(key)
      const ids = new Set(threads.map((thread) => thread.id))
      queryClient.setQueryData<Conversation | undefined>(key, (old) =>
        old
          ? {
              ...old,
              threads: old.threads.map((thread) =>
                ids.has(thread.id) ? { ...thread, resolved: true } : thread
              ),
            }
          : old
      )
      return { previous }
    },
    onError: (_error, _threads, context) =>
      queryClient.setQueryData(key, context?.previous),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key })
      void queryClient.invalidateQueries({ queryKey: ["review-page-pr"] })
    },
  })
}
