import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { api } from "@/lib/api"

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
    meta: { errorTitle: "Couldn't update label" },
    onError: (_error, _change, previous) => {
      queryClient.setQueryData(queryKey, previous)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      {labels.data?.selected.map((label) => (
        <span
          key={label.name}
          className="inline-flex items-center gap-1 rounded-full border border-default px-2 py-0.5 text-xs"
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
        <PopoverTrigger asChild>
          <Button size="xs" color="secondary" variant="plain">
            Labels
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-72 p-space-2">
          <Input
            size="sm"
            leftIcon={MagnifyingGlassIcon}
            aria-label="Search labels"
            placeholder="Search labels…"
            value={search}
            onChange={setSearch}
          />
          {labels.isPending && (
            <p className="p-2 text-xs text-secondary">Loading labels…</p>
          )}
          {labels.error && (
            <p role="alert" className="p-2 text-xs text-error-secondary">
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
                  <Checkbox
                    key={label.name}
                    containerClassName="rounded-sm p-space-2 hover:bg-elevated-hover"
                    title={label.description ?? undefined}
                    checked={selected}
                    disabled={change.isPending}
                    onCheckedChange={() =>
                      change.mutate({ name: label.name, selected: !selected })
                    }
                    label={
                      <span className="flex items-center gap-space-2 text-xs">
                        <span
                          className="size-2 shrink-0 rounded-full"
                          style={{ backgroundColor: `#${label.color}` }}
                        />
                        {label.name}
                      </span>
                    }
                  />
                )
              })}
            {labels.data?.available.length === 0 && (
              <p className="p-2 text-xs text-secondary">
                No repository labels.
              </p>
            )}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
