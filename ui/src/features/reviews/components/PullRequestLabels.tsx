import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export function PullRequestLabels({
  owner,
  repo,
  number,
}: {
  owner: string
  repo: string
  number: number
}) {
  const queryClient = useQueryClient()
  const queryKey = ["pullRequestLabels", owner, repo, number]
  const labels = useQuery({
    queryKey,
    queryFn: () => api.getPullRequestLabels(owner, repo, number),
  })
  const [search, setSearch] = useState("")
  const change = useMutation({
    mutationFn: ({ name, selected }: { name: string; selected: boolean }) =>
      api.changePullRequestLabel(owner, repo, number, name, selected),
    onMutate: async ({ name, selected }) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<typeof labels.data>(queryKey)
      if (previous) {
        const label = previous.available.find((item) => item.name === name)
        queryClient.setQueryData(queryKey, {
          ...previous,
          selected:
            selected && label
              ? [...previous.selected, label]
              : previous.selected.filter((item) => item.name !== name),
        })
      }
      return previous
    },
    onError: (error, _change, previous) => {
      queryClient.setQueryData(queryKey, previous)
      toast.error(error.message)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      {labels.data?.selected.map((label) => (
        <span
          key={label.name}
          className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs"
          title={label.description ?? undefined}
        >
          <span
            className="size-2 rounded-full"
            style={{ backgroundColor: `#${label.color}` }}
          />
          {label.name}
        </span>
      ))}
      <Popover>
        <PopoverTrigger
          render={
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" />
          }
        >
          Labels
        </PopoverTrigger>
        <PopoverContent align="start" className="w-72 p-2">
          <Input
            aria-label="Search labels"
            placeholder="Search labels…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {labels.isPending && (
            <p className="p-2 text-xs text-muted-foreground">Loading labels…</p>
          )}
          {labels.error && (
            <p role="alert" className="p-2 text-xs text-destructive">
              {labels.error.message}
            </p>
          )}
          <div className="mt-2 max-h-64 overflow-y-auto">
            {labels.data?.available
              .filter((label) =>
                label.name.toLowerCase().includes(search.toLowerCase())
              )
              .map((label) => {
                const selected = labels.data.selected.some(
                  (item) => item.name === label.name
                )
                return (
                  <label
                    key={label.name}
                    className="flex cursor-pointer items-center gap-2 rounded p-2 text-xs hover:bg-muted"
                    title={label.description ?? undefined}
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={change.isPending}
                      onChange={() =>
                        change.mutate({ name: label.name, selected: !selected })
                      }
                    />
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ backgroundColor: `#${label.color}` }}
                    />
                    {label.name}
                  </label>
                )
              })}
            {labels.data?.available.length === 0 && (
              <p className="p-2 text-xs text-muted-foreground">
                No repository labels.
              </p>
            )}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
