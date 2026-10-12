import {
  MagnifyingGlassRegularIcon,
  TagRegularIcon,
} from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { api } from "@/lib/api"

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
    <div className="flex flex-wrap items-center gap-space-1">
      {shown.map((label) => (
        <span
          key={label.name}
          className="inline-flex h-5 items-center gap-space-1 rounded-full border border-default px-space-1 text-xxs"
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
        <PopoverTrigger asChild>
          <Button
            size="xs"
            color="secondary"
            variant="plain"
            leftDecorator={TagRegularIcon}
            aria-label="Labels"
            onPointerEnter={() => setWanted(true)}
            onFocus={() => setWanted(true)}
          >
            {shown.length === 0 ? "Add labels" : ""}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-72 p-space-2">
          <Input
            size="sm"
            leftIcon={MagnifyingGlassRegularIcon}
            aria-label="Search labels"
            placeholder="Search labels…"
            value={search}
            onChange={setSearch}
          />
          {catalog.isPending && (
            <p className="p-space-2 text-xs text-secondary">Loading labels…</p>
          )}
          {catalog.error && (
            <p role="alert" className="p-space-2 text-xs text-error-secondary">
              {catalog.error.message}
            </p>
          )}
          <div className="mt-space-2 max-h-64 overflow-y-auto">
            {catalog.data?.available
              .filter((label) =>
                label.name.toLowerCase().includes(search.toLowerCase())
              )
              .map((label) => {
                const selected = catalog.data.selected.some(
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
            {catalog.data?.available.length === 0 && (
              <p className="p-space-2 text-xs text-secondary">
                No repository labels.
              </p>
            )}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
