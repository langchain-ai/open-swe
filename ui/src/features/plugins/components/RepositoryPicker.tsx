import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { useMemo, useState } from "react"

import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type { RepositoryPick } from "@/features/plugins/lib/bridge"
import { useRefreshRepos, useRepos } from "@/lib/profile"
import { useIsInBaseOrg } from "@/lib/session"
import { cn } from "@/lib/utils"

const OWNER_REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

/**
 * The list a plugin's `repositories.pick` shows: the person chooses from the
 * repositories they can read, and the plugin gets only those. Repositories in
 * this Open SWE's own organizations go by name alone.
 */
export function RepositoryPicker({
  plugin,
  pick,
  armed,
  onAnswer,
}: {
  plugin: InstalledPlugin
  pick: RepositoryPick
  armed: boolean
  onAnswer: (chosen: ReadonlyArray<string> | null) => void
}) {
  const repos = useRepos()
  const refresh = useRefreshRepos()
  const isInBaseOrg = useIsInBaseOrg()
  const [query, setQuery] = useState("")
  const [chosen, setChosen] = useState<ReadonlyArray<string>>(() =>
    pick.multiple ? pick.suggested : pick.suggested.slice(0, 1)
  )

  const options = useMemo(() => {
    const readable = (repos.data?.repositories ?? [])
      .filter((repo) => !repo.archived)
      .map((repo) => repo.full_name.toLowerCase())
    const all = [...new Set([...pick.suggested, ...readable])]
    const q = query.trim().toLowerCase()
    const matching = all.filter((repo) => repo.includes(q))
    // Someone may read a repository the web UI has not listed for them.
    if (OWNER_REPO.test(q) && !all.includes(q)) matching.unshift(q)
    return matching
  }, [pick.suggested, query, repos.data])

  const toggle = (repo: string) =>
    setChosen((prev) =>
      prev.includes(repo)
        ? prev.filter((other) => other !== repo)
        : pick.multiple
          ? [...prev, repo]
          : [repo]
    )

  return (
    <>
      <div className="flex flex-col overflow-hidden rounded-md border border-default">
        <div className="border-b border-default">
          <Input
            autoFocus
            size="sm"
            variant="plain"
            value={query}
            onChange={setQuery}
            debounceMs={0}
            placeholder="Search or type owner/repo"
            aria-label="Search repositories"
            leftDecorator={<MagnifyingGlassIcon size={14} />}
            rightDecorator={
              <IconButton
                icon={ArrowsClockwiseIcon}
                label="Refresh repositories"
                size="xs"
                color="secondary"
                variant="plain"
                loading={refresh.isPending}
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
              />
            }
          />
        </div>
        <div className="flex max-h-72 flex-col overflow-y-auto py-space-1">
          {options.length === 0 ? (
            <p className="px-space-3 py-space-2 text-xs text-secondary">
              {repos.isPending
                ? "Loading repositories…"
                : query.trim()
                  ? "No matches; type the full owner/repo"
                  : "Type a repository as owner/repo"}
            </p>
          ) : (
            options.map((repo) => {
              const shared = plugin.repositories.includes(repo)
              return (
                <label
                  key={repo}
                  className={cn(
                    "flex cursor-pointer items-center gap-space-2 px-space-3 py-space-1 text-xs hover:bg-surface-level-1-hover",
                    chosen.includes(repo) ? "text-primary" : "text-secondary"
                  )}
                >
                  <Checkbox
                    size="sm"
                    checked={chosen.includes(repo)}
                    onCheckedChange={() => toggle(repo)}
                    aria-label={repo}
                  />
                  <span className="min-w-0 flex-1 truncate" title={repo}>
                    {isInBaseOrg(repo) ? repo.split("/")[1] : repo}
                  </span>
                  {shared && (
                    <Badge size="xxs" color="secondary">
                      Shared
                    </Badge>
                  )}
                  {pick.suggested.includes(repo) && !shared && (
                    <Badge size="xxs" color="secondary">
                      Asked for
                    </Badge>
                  )}
                </label>
              )
            })
          )}
        </div>
      </div>
      <div className="flex justify-end gap-space-2">
        <Button size="sm" variant="outlined" onClick={() => onAnswer(null)}>
          Cancel
        </Button>
        <Button
          size="sm"
          disabled={!armed || chosen.length === 0}
          onClick={() => onAnswer(chosen)}
        >
          {chosen.length > 1 ? `Share ${chosen.length}` : "Share"}
        </Button>
      </div>
    </>
  )
}
