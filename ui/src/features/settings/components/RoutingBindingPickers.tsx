import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"

import { RepositoryLabels } from "@/lib/repo"
import { useRepos } from "@/lib/profile"
import { BindingPicker, type PickerItem } from "./BindingPicker"

const REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

interface BindingPickerProps {
  selected: Array<string>
  onChange: (ids: Array<string>) => void
  disabled?: boolean
}

export function RepositoryPicker({
  selected,
  onChange,
  disabled,
}: BindingPickerProps) {
  const repos = useRepos()
  const repositories = repos.data?.repositories ?? []
  const labels = new RepositoryLabels([
    ...repositories.map((repo) => repo.full_name),
    ...selected,
  ])
  const reposByName = new Map(
    repositories.map((repo) => [repo.full_name, repo])
  )
  const items: Array<PickerItem> = repositories.map((repo) => ({
    id: repo.full_name,
    label: labels.label(repo.full_name),
    meta: `${repo.private ? "Private" : "Public"}${repo.archived ? " archive" : ""}`,
    icon: repo.private ? (
      <LockSimpleIcon size={14} weight="regular" />
    ) : (
      <FolderIcon size={14} weight="regular" />
    ),
  }))
  return (
    <BindingPicker
      triggerLabel="Choose repositories"
      title="Bound Repositories"
      description="Bind repositories for inbound GitHub and Linear routing. This does not grant access or change preloads."
      noun="repository"
      pluralNoun="repositories"
      items={items}
      selected={selected}
      onChange={onChange}
      searchPlaceholder="Search repositories"
      matchesItem={(item, search) => labels.matches(item.id, search)}
      filter={{
        label: "Exclude archived",
        matches: (item) => !reposByName.get(item.id)?.archived,
      }}
      filters={[
        {
          label: "Public",
          matches: (item) => reposByName.get(item.id)?.private === false,
        },
        {
          label: "Internal",
          matches: (item) => reposByName.get(item.id)?.private === true,
        },
      ]}
      manual={{
        label: "Add a repository by name",
        placeholder: "owner/repo",
        normalize: (raw) => {
          const value = raw.trim()
          return REPO_PATTERN.test(value) ? value : null
        },
        invalidHint: "Use the owner/repo form.",
      }}
      loading={repos.isLoading}
      loadError={
        repos.isError
          ? "Could not load the installation's repositories; add them by name."
          : null
      }
      disabled={disabled}
    />
  )
}
