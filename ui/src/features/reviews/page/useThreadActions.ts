import { useMutation, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"
import {
  replyToReviewThread,
  setReviewThreadResolved,
  type Conversation,
  type ReviewThread,
  type ThreadComment,
} from "@/features/reviews/lib/conversationApi"
import {
  reviewKeys,
  type PullRequestRef,
} from "@/features/reviews/lib/reviewKeys"
import { useRefreshPullRequest } from "./queries"

type ThreadChange = (thread: ReviewThread) => ReviewThread

function patchThreads(
  conversation: Conversation,
  ids: ReadonlySet<number>,
  change: ThreadChange
): Conversation {
  return {
    ...conversation,
    threads: conversation.threads.map((thread) =>
      ids.has(thread.id) ? change(thread) : thread
    ),
  }
}

function useConversationCache(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const pull = useRefreshPullRequest(pr)
  const key = reviewKeys.conversation(pr)
  return {
    optimistic: (ids: ReadonlySet<number>, change: ThreadChange) =>
      optimisticUpdate<Conversation>(queryClient, key, (current) =>
        patchThreads(current, ids, change)
      ),
    patch: (ids: ReadonlySet<number>, change: ThreadChange) =>
      queryClient.setQueryData<Conversation>(key, (current) =>
        current ? patchThreads(current, ids, change) : current
      ),
    refresh: (conversation: boolean) => {
      pull.status()
      if (conversation) void queryClient.invalidateQueries({ queryKey: key })
    },
  }
}

/** Reply to and resolve one inline thread on GitHub, updating the page before GitHub answers. */
export function useThreadActions(pr: PullRequestRef, thread: ReviewThread) {
  const login = useSession().data?.login
  const cache = useConversationCache(pr)
  const only = new Set([thread.id])

  const reply = useMutation({
    mutationFn: (body: string) => replyToReviewThread(pr, thread.id, body),
    meta: { errorTitle: "Couldn't post the reply" },
    onMutate: async (body) => {
      const optimistic: ThreadComment = {
        id: -Date.now(),
        review_id: null,
        author: login
          ? {
              login,
              avatar_url: `https://github.com/${login}.png?size=40`,
              bot: false,
              posted_by: null,
            }
          : null,
        created_at: new Date().toISOString(),
        body,
        html_url: "",
      }
      const undo = await cache.optimistic(only, (t) => ({
        ...t,
        comments: [...t.comments, optimistic],
      }))
      return { undo, optimisticId: optimistic.id }
    },
    onError: (_error, _body, context) => context?.undo(),
    onSuccess: (created, _body, context) =>
      cache.patch(only, (t) => ({
        ...t,
        comments: [
          t.comments[0],
          ...t.comments
            .slice(1)
            .map((c) => (c.id === context?.optimisticId ? created : c)),
        ],
      })),
  })

  const resolve = useMutation({
    mutationFn: (resolved: boolean) => {
      if (!thread.node_id) throw new Error("GitHub didn't name this thread")
      return setReviewThreadResolved(pr, thread.node_id, resolved)
    },
    meta: { errorTitle: "Couldn't update the conversation" },
    onMutate: async (resolved) => ({
      undo: await cache.optimistic(only, (t) => ({ ...t, resolved })),
    }),
    onError: (_error, _resolved, context) => context?.undo(),
    onSettled: () => cache.refresh(false),
  })

  return { reply, resolve }
}

/** Resolve several threads at once, e.g. every outdated one the author has already answered. */
export function useResolveThreads(pr: PullRequestRef) {
  const cache = useConversationCache(pr)
  return useMutation({
    mutationFn: async (threads: ReadonlyArray<ReviewThread>) => {
      const nodeIds = threads.flatMap((thread) =>
        thread.node_id ? [thread.node_id] : []
      )
      const { failed } = await api.resolveReviewThreads(
        `${pr.owner}/${pr.repo}`,
        pr.number,
        nodeIds
      )
      if (failed.length)
        throw new Error(
          `${failed.length} of ${nodeIds.length} conversations stayed open`
        )
    },
    meta: { errorTitle: "Couldn't resolve every conversation" },
    onMutate: async (threads) => ({
      undo: await cache.optimistic(
        new Set(threads.map((thread) => thread.id)),
        (thread) => ({ ...thread, resolved: true })
      ),
    }),
    onError: (_error, _threads, context) => context?.undo(),
    // A partial failure leaves some resolved, so read back what GitHub holds.
    onSettled: () => cache.refresh(true),
  })
}
