import { Badge } from "@langchain/macaw-components/Badge"
import {
  CheckIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import { useState } from "react"
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@langchain/macaw-components/Command"
import { IconButton } from "@langchain/macaw-components/IconButton"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderDashedIcon } from "@phosphor-icons/react/dist/ssr/FolderDashed"
import { FolderPlusIcon } from "@phosphor-icons/react/dist/ssr/FolderPlus"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { UsersIcon } from "@phosphor-icons/react/dist/ssr/Users"

import {
  COMPOSER_TRIGGER_CLASS_NAME,
  ComposerControlChevron,
  ComposerTriggerIcon,
} from "@/features/agents/components/composer/ComposerControl"
import type { Project } from "@/features/projects/lib/projects"
import { useRefreshRepos } from "@/lib/profile"
import { RepositoryLabels } from "@/lib/repo"
import { cn } from "@/lib/utils"

const ROW_CLASS_NAME =
  "gap-space-2 rounded-md px-space-2 py-space-1 text-xs text-primary"

const ROW_ICON_CLASS_NAME = "size-3.5 shrink-0 text-icon-secondary"

const GROUP_CLASS_NAME =
  "[&_[cmdk-group-heading]]:px-space-2 [&_[cmdk-group-heading]]:py-space-1 [&_[cmdk-group-heading]]:text-xxs [&_[cmdk-group-heading]]:text-tertiary"

type RepoOption = { full_name: string; private?: boolean; archived?: boolean }

function Selected() {
  return <CheckIcon className={ROW_ICON_CLASS_NAME} weight="regular" />
}

/**
 * Picks what a new cloud thread works in: one of the person's projects, or a
 * repository, which is a project with git. Typing a name no project has
 * offers to create it. Without `repos`, as while GitHub is unlinked, it lists
 * projects only.
 */
export function ProjectSelector({
  projects,
  selectedId,
  onSelect,
  onCreate,
  repos,
  selectedRepo = null,
  onRepoChange,
  side = "top",
}: {
  projects: Array<Project>
  selectedId: string | null
  onSelect: (projectId: string | null) => void
  onCreate: (name: string) => void
  repos?: Array<RepoOption>
  selectedRepo?: string | null
  /** Picking a repository leaves any project, so the thread is filed under it. */
  onRepoChange?: (repo: string | null) => void
  side?: "top" | "bottom"
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const refresh = useRefreshRepos()
  const selected = projects.find((project) => project.id === selectedId)
  const needle = query.trim().toLowerCase()
  const matches = needle
    ? projects.filter((project) => project.name.toLowerCase().includes(needle))
    : projects
  const creatable =
    needle && !projects.some((project) => project.name.toLowerCase() === needle)
  // A repository bound to one of the projects is reached through the project.
  const bound = new Set(
    projects.flatMap((project) =>
      project.repo_full_name ? [project.repo_full_name.toLowerCase()] : []
    )
  )
  const labels = new RepositoryLabels([
    ...(repos ?? []).map((repo) => repo.full_name),
    ...(selectedRepo ? [selectedRepo] : []),
  ])
  const repoMatches = (repos ?? []).filter(
    (repo) =>
      !repo.archived &&
      !bound.has(repo.full_name.toLowerCase()) &&
      labels.matches(repo.full_name, query)
  )
  const label =
    selected?.name ??
    (onRepoChange && selectedRepo ? labels.label(selectedRepo) : "No project")

  const toggle = (next: boolean) => {
    if (!next) setQuery("")
    setOpen(next)
  }
  const choose = (action: () => void) => {
    action()
    toggle(false)
  }
  const chooseRepo = (repo: string | null) => {
    onSelect(null)
    onRepoChange?.(repo)
  }

  return (
    <Popover open={open} onOpenChange={toggle}>
      <PopoverTrigger
        className={cn(COMPOSER_TRIGGER_CLASS_NAME, "max-w-[260px] min-w-0")}
        title={selected?.repo_full_name ?? selectedRepo ?? undefined}
      >
        <ComposerTriggerIcon icon={FolderIcon} />
        <span className="truncate">{label}</span>
        <ComposerControlChevron />
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side={side}
        sideOffset={7}
        className="w-80 overflow-hidden p-0"
      >
        <Command label="Projects" loop shouldFilter={false}>
          <CommandInput
            autoFocus
            className="rounded-none border-0 border-b border-muted bg-transparent focus-within:border-muted"
            leftDecorator={
              <MagnifyingGlassRegularIcon
                className="text-icon-secondary"
                size={14}
              />
            }
            rightDecorator={
              repos && (
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
              )
            }
            onValueChange={setQuery}
            placeholder="Search or name a new project"
            size="sm"
            value={query}
            variant="plain"
          />
          <CommandList className="max-h-80 p-space-1">
            {creatable && (
              <CommandItem
                className={ROW_CLASS_NAME}
                onSelect={() => choose(() => onCreate(query.trim()))}
                size="sm"
                value="create-project"
              >
                <FolderPlusIcon className={ROW_ICON_CLASS_NAME} />
                <span className="min-w-0 flex-1 truncate">
                  Create project “{query.trim()}”
                </span>
              </CommandItem>
            )}
            {!needle && (
              <CommandItem
                className={ROW_CLASS_NAME}
                onSelect={() => choose(() => chooseRepo(null))}
                size="sm"
                title="The agent works in a folder of its own"
                value="no-project"
              >
                <FolderDashedIcon className={ROW_ICON_CLASS_NAME} />
                <span className="min-w-0 flex-1 truncate">No project</span>
                {!selected && !(onRepoChange && selectedRepo) && <Selected />}
              </CommandItem>
            )}
            {matches.length > 0 && (
              <CommandGroup heading="Projects" className={GROUP_CLASS_NAME}>
                {matches.map((project) => (
                  <CommandItem
                    className={ROW_CLASS_NAME}
                    key={project.id}
                    onSelect={() => choose(() => onSelect(project.id))}
                    size="sm"
                    title={project.repo_full_name ?? undefined}
                    value={project.id}
                  >
                    <FolderIcon className={ROW_ICON_CLASS_NAME} />
                    <span className="min-w-0 flex-1 truncate">
                      {project.name}
                    </span>
                    {project.members.length > 1 && (
                      <UsersIcon
                        aria-label="Shared"
                        className={ROW_ICON_CLASS_NAME}
                      />
                    )}
                    {project.id === selectedId && <Selected />}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {onRepoChange && repoMatches.length > 0 && (
              <CommandGroup heading="Repositories" className={GROUP_CLASS_NAME}>
                {repoMatches.map((repo) => (
                  <CommandItem
                    className={ROW_CLASS_NAME}
                    key={repo.full_name}
                    onSelect={() => choose(() => chooseRepo(repo.full_name))}
                    size="sm"
                    title={repo.full_name}
                    value={`repo:${repo.full_name}`}
                  >
                    <GitBranchIcon className={ROW_ICON_CLASS_NAME} />
                    <span className="min-w-0 flex-1 truncate">
                      {labels.label(repo.full_name)}
                    </span>
                    {repo.private !== undefined && (
                      <Badge
                        color="secondary"
                        size="xxs"
                        rounded="xs"
                        className="shrink-0"
                      >
                        {repo.private ? "Private" : "Public"}
                      </Badge>
                    )}
                    {!selected && repo.full_name === selectedRepo && (
                      <Selected />
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {!needle && projects.length === 0 && !repos?.length && (
              <p className="px-space-2 py-space-1 text-xs text-secondary">
                Type a name to create your first project
              </p>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
