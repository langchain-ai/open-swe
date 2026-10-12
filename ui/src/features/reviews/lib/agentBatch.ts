/**
 * Comments queued for one agent run per pull request, sent together on submit.
 * Queued items persist per browser as an unsent draft; sent ones are session-only.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"
import type { SelectedLineRange } from "@pierre/diffs"

import type { AgentBatchComment } from "@/lib/api"
import { buildCommentPayload } from "@/features/reviews/lib/lineRange"
import { api } from "@/lib/api"

export type BatchItem =
  | {
      kind: "line"
      id: string
      path: string
      range: SelectedLineRange
      body: string
    }
  | { kind: "thread"; id: string; commentUrl: string; instructions: string }

export type BatchItemState = "queued" | "sent"

interface QueuedItem {
  item: BatchItem
  state: BatchItemState
}

interface AgentBatchState {
  byPr: Record<string, Array<QueuedItem>>
  add: (pr: string, item: BatchItem) => void
  remove: (pr: string, id: string) => void
  discard: (pr: string) => void
  mark: (pr: string, ids: ReadonlyArray<string>, state: BatchItemState) => void
}

const STORAGE_KEY = "open-swe:agent-comment-batch"
const STORAGE_VERSION = 1

function isRange(value: unknown): value is SelectedLineRange {
  if (!value || typeof value !== "object") return false
  const range = value as Record<string, unknown>
  return typeof range.start === "number" && typeof range.end === "number"
}

function parseItem(value: unknown): BatchItem | null {
  if (!value || typeof value !== "object") return null
  const item = value as Record<string, unknown>
  if (typeof item.id !== "string") return null
  if (
    item.kind === "line" &&
    typeof item.path === "string" &&
    typeof item.body === "string" &&
    isRange(item.range)
  )
    return {
      kind: "line",
      id: item.id,
      path: item.path,
      body: item.body,
      range: item.range,
    }
  if (
    item.kind === "thread" &&
    typeof item.commentUrl === "string" &&
    typeof item.instructions === "string"
  )
    return {
      kind: "thread",
      id: item.id,
      commentUrl: item.commentUrl,
      instructions: item.instructions,
    }
  return null
}

/** Persisted batches are untrusted input: anything unrecognized is dropped. */
function migrate(persisted: unknown): Pick<AgentBatchState, "byPr"> {
  const raw =
    persisted && typeof persisted === "object" && "byPr" in persisted
      ? persisted.byPr
      : null
  if (!raw || typeof raw !== "object") return { byPr: {} }
  const byPr: Record<string, Array<QueuedItem>> = {}
  for (const [pr, entries] of Object.entries(raw as Record<string, unknown>)) {
    if (!Array.isArray(entries)) continue
    const items = entries
      .map((entry: unknown) =>
        entry && typeof entry === "object" && "item" in entry
          ? parseItem(entry.item)
          : null
      )
      .filter((item): item is BatchItem => item !== null)
      .map((item) => ({ item, state: "queued" as const }))
    if (items.length) byPr[pr] = items
  }
  return { byPr }
}

export const useAgentBatchStore = create<AgentBatchState>()(
  persist(
    (set) => ({
      byPr: {},
      add: (pr, item) =>
        set((state) => ({
          byPr: {
            ...state.byPr,
            [pr]: [...(state.byPr[pr] ?? []), { item, state: "queued" }],
          },
        })),
      remove: (pr, id) =>
        set((state) => ({
          byPr: {
            ...state.byPr,
            [pr]: (state.byPr[pr] ?? []).filter(
              (entry) => entry.item.id !== id
            ),
          },
        })),
      discard: (pr) =>
        set((state) => ({
          byPr: {
            ...state.byPr,
            [pr]: (state.byPr[pr] ?? []).filter(
              (entry) => entry.state !== "queued"
            ),
          },
        })),
      mark: (pr, ids, next) =>
        set((state) => ({
          byPr: {
            ...state.byPr,
            [pr]: (state.byPr[pr] ?? []).map((entry) =>
              ids.includes(entry.item.id) ? { ...entry, state: next } : entry
            ),
          },
        })),
    }),
    {
      name: STORAGE_KEY,
      version: STORAGE_VERSION,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        byPr: Object.fromEntries(
          Object.entries(state.byPr).map(([pr, entries]) => [
            pr,
            entries.filter((entry) => entry.state === "queued"),
          ])
        ),
      }),
      migrate,
      merge: (persisted, current) => ({ ...current, ...migrate(persisted) }),
    }
  )
)

const noItems: Array<QueuedItem> = []

export function useAgentBatch(pr: string): Array<QueuedItem> {
  return useAgentBatchStore((state) => state.byPr[pr] ?? noItems)
}

function toPayload(item: BatchItem): AgentBatchComment {
  if (item.kind === "thread")
    return {
      kind: "thread",
      comment_url: item.commentUrl,
      instructions: item.instructions,
    }
  const { path, line, side, start_line, body } = buildCommentPayload(
    item.path,
    item.range,
    item.body
  )
  return { kind: "line", path, line, side, start_line, body }
}

/** Sends every queued comment as one agent run, showing them sent immediately. */
export function useSubmitAgentBatch(pr: { repo: string; number: number }) {
  const key = `${pr.repo}#${pr.number}`
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (items: Array<BatchItem>) =>
      api.sendCommentsToAgent(pr.repo, pr.number, items.map(toPayload)),
    meta: { errorTitle: "Couldn't send comments to agent" },
    onMutate: (items) =>
      useAgentBatchStore.getState().mark(
        key,
        items.map((item) => item.id),
        "sent"
      ),
    onError: (_error, items) =>
      useAgentBatchStore.getState().mark(
        key,
        items.map((item) => item.id),
        "queued"
      ),
    onSuccess: (result, items) => {
      const count = `${items.length} comment${items.length === 1 ? "" : "s"}`
      toast.success(
        result.already_running
          ? `Queued ${count} behind the running agent on ${key}`
          : `Sent ${count} to the agent for ${key}`
      )
      void queryClient.invalidateQueries({ queryKey: ["pr-thread-status"] })
    },
  })
}
