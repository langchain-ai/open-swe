import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { agentsRequest } from "@/features/agents/lib/api"

export interface InboxSnooze {
  key: string
  until_ms: number
  snoozed_at_ms: number
}

const inboxSnoozesKey = ["inbox", "snoozes"] as const

const inboxSnoozesApi = {
  list: () => agentsRequest<Array<InboxSnooze>>("/inbox/snoozes"),
  snooze: (key: string, untilMs: number) =>
    agentsRequest<InboxSnooze>("/inbox/snoozes", {
      method: "PUT",
      body: JSON.stringify({ key, until_ms: untilMs }),
    }),
  wake: (key: string) =>
    agentsRequest<void>(`/inbox/snoozes?key=${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),
}

export function useInboxSnoozes() {
  return useQuery({
    queryKey: inboxSnoozesKey,
    queryFn: inboxSnoozesApi.list,
  })
}

/** Snooze an item until `untilMs`, or wake it when `untilMs` is null. */
export function useSetInboxSnooze() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      key,
      untilMs,
    }: {
      key: string
      untilMs: number | null
    }) => {
      if (untilMs === null) await inboxSnoozesApi.wake(key)
      else await inboxSnoozesApi.snooze(key, untilMs)
    },
    meta: { errorTitle: "Couldn't update snooze" },
    onMutate: async ({ key, untilMs }) => {
      await queryClient.cancelQueries({ queryKey: inboxSnoozesKey })
      const previous =
        queryClient.getQueryData<Array<InboxSnooze>>(inboxSnoozesKey)
      queryClient.setQueryData<Array<InboxSnooze>>(
        inboxSnoozesKey,
        (current = []) => [
          ...current.filter((snooze) => snooze.key !== key),
          ...(untilMs === null
            ? []
            : [{ key, until_ms: untilMs, snoozed_at_ms: Date.now() }]),
        ]
      )
      return { previous }
    },
    onError: (_error, _vars, context) =>
      queryClient.setQueryData(inboxSnoozesKey, context?.previous),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: inboxSnoozesKey }),
  })
}

export interface SnoozeOption {
  shortcut: string
  label: string
  until: (now: Date) => Date
}

function atNine(day: Date): Date {
  const at = new Date(day)
  at.setHours(9, 0, 0, 0)
  return at
}

export const SNOOZE_OPTIONS: ReadonlyArray<SnoozeOption> = [
  {
    shortcut: "1",
    label: "In an hour",
    until: (now) => new Date(now.getTime() + 60 * 60 * 1000),
  },
  {
    shortcut: "2",
    label: "Later today",
    until: (now) => new Date(now.getTime() + 3 * 60 * 60 * 1000),
  },
  {
    shortcut: "3",
    label: "Tomorrow",
    until: (now) =>
      atNine(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)),
  },
  {
    shortcut: "4",
    label: "Next week",
    until: (now) =>
      atNine(
        new Date(
          now.getFullYear(),
          now.getMonth(),
          now.getDate() + ((8 - now.getDay()) % 7 || 7)
        )
      ),
  },
]

export function formatSnoozeTime(at: Date, now: Date): string {
  const time = at.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  })
  if (at.toDateString() === now.toDateString()) return time
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  if (at.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`
  return `${at.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${time}`
}
