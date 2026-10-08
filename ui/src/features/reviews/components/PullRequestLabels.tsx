import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { TagIcon } from "@phosphor-icons/react"

import { api } from "@/lib/api"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

interface Label {
  name: string
  color: string | null
  description?: string | null
}

/**
 * The PR's labels, editable in place. The PR's own labels come with the page;
 * the repository's catalog loads only when the picker is about to open.
 */
export function PullRequestLabels({
  owner,
  repo,
  number,
  labels: initial,
}: {
  owner: string
  repo: string
  number: number
  labels: ReadonlyArray<Label>
}) {
  const queryClient = useQueryClient()
  const queryKey = ["pullRequestLabels", owner, repo, number]
  const [wanted, setWanted] = useState(false)
  const catalog = useQuery({
    queryKey,
    queryFn: () => api.getPullRequestLabels(owner, repo, number),
    enabled: wanted,
  })
  const [search, setSearch] = useState("")
  const change = useMutation({
    mutationFn: ({ name, selected }: { name: string; selected: boolean }) =>
      api.changePullRequestLabel(owner, repo, number, name, selected),
    onMutate: async ({ name, selected }) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<typeof catalog.data>(queryKey)
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
    meta: { errorTitle: "Couldn't update label" },
    onError: (_error, _change, previous) => {
      queryClient.setQueryData(queryKey, previous)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })
  const shown: ReadonlyArray<Label> = catalog.data?.selected ?? initial
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((label) => (
        <span
          key={label.name}
          className="inline-flex h-5 items-center gap-1 rounded-full border border-border px-1.5 text-[11px]"
          title={label.description ?? undefined}
        >
          <span
            className="size-2 rounded-full"
            style={{ backgroundColor: `#${label.color ?? "888888"}` }}
          />
          {label.name}
        </span>
      ))}
      <Popover onOpenChange={(open) => open && setWanted(true)}>
        <PopoverTrigger
          onPointerEnter={() => setWanted(true)}
          onFocus={() => setWanted(true)}
          render={
            <button
              type="button"
              aria-label="Labels"
              className="inline-flex h-5 items-center gap-1 rounded-full px-1.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
            />
          }
        >
          <TagIcon className="size-3" />
          {shown.length === 0 && "Add labels"}
        </PopoverTrigger>
        <PopoverContent align="end" className="w-72 p-2">
          <Input
            aria-label="Search labels"
            placeholder="Search labels…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {catalog.isPending && (
            <p className="p-2 text-xs text-muted-foreground">Loading labels…</p>
          )}
          {catalog.error && (
            <p role="alert" className="p-2 text-xs text-destructive">
              {catalog.error.message}
            </p>
          )}
          <div className="mt-2 max-h-64 overflow-y-auto">
            {catalog.data?.available
              .filter((label) =>
                label.name.toLowerCase().includes(search.toLowerCase())
              )
              .map((label) => {
                const selected = catalog.data.selected.some(
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
            {catalog.data?.available.length === 0 && (
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
