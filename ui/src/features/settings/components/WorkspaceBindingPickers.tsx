import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"

import {
  RefreshSlackChannels,
  SlackChannelIcon,
} from "@/components/SlackChannelCombobox"
import { type WorkspaceOption } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import {
  rankSlackChannels,
  rememberSlackChannels,
  useRecentSlackChannels,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import {
  OwnershipPicker,
  type PickerItem,
  type PickerOwner,
} from "./OwnershipPicker"

function ownersOf(
  workspaces: Array<WorkspaceOption>,
  bindings: (workspace: WorkspaceOption) => Array<string>
): Map<string, PickerOwner> {
  const owners = new Map<string, PickerOwner>()
  for (const workspace of workspaces) {
    for (const id of bindings(workspace)) {
      owners.set(id.toLowerCase(), {
        slug: workspace.slug,
        name: workspace.name,
      })
    }
  }
  return owners
}

const REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

interface BindingPickerProps {
  selected: Array<string>
  onChange: (ids: Array<string>) => void
  /** The workspace being edited; null while creating one. */
  workspaceSlug: string | null
  workspaces: Array<WorkspaceOption>
  disabled?: boolean
}

export function RepositoryPicker({
  selected,
  onChange,
  workspaceSlug,
  workspaces,
  disabled,
}: BindingPickerProps) {
  const repos = useRepos()
  const repositories = repos.data?.repositories ?? []
  const reposByName = new Map(
    repositories.map((repo) => [repo.full_name, repo])
  )
  const owners = ownersOf(workspaces, (workspace) => workspace.repos)
  const items: Array<PickerItem> = repositories.map((repo) => ({
    id: repo.full_name,
    label: repo.full_name,
    meta: `${repo.private ? "Private" : "Public"}${repo.archived ? " archive" : ""}`,
    icon: repo.private ? (
      <LockSimpleIcon size={14} weight="regular" />
    ) : (
      <FolderIcon size={14} weight="regular" />
    ),
    owner: owners.get(repo.full_name.toLowerCase()) ?? null,
  }))
  return (
    <OwnershipPicker
      triggerLabel="Choose repositories"
      title="Bound Repositories"
      description="Events on these repositories run in this workspace, and its image preloads them. A repository is bound to one workspace."
      noun="repository"
      pluralNoun="repositories"
      items={items}
      selected={selected}
      workspaceSlug={workspaceSlug}
      onChange={onChange}
      searchPlaceholder="Search repositories"
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

export function SlackChannelPicker({
  selected,
  onChange,
  workspaceSlug,
  workspaces,
  disabled,
}: BindingPickerProps) {
  const directory = useSlackChannelDirectory(true)
  const recent = useRecentSlackChannels()
  const owners = ownersOf(
    workspaces,
    (workspace) => workspace.slack_channel_ids
  )
  const items: Array<PickerItem> = rankSlackChannels(
    directory.data?.channels ?? [],
    "",
    recent
  ).map((channel) => ({
    id: channel.id,
    label: `#${channel.name}`,
    meta: [
      channel.is_private
        ? "private"
        : channel.is_ext_shared
          ? "shared"
          : "public",
      channel.num_members === null ? null : `${channel.num_members} members`,
      recent.includes(channel.id) ? "recently used" : null,
    ]
      .filter((part): part is string => part !== null)
      .join(" · "),
    icon: <SlackChannelIcon channel={channel} />,
    owner: owners.get(channel.id.toLowerCase()) ?? null,
    disabled: !channel.is_member,
    warning: channel.is_member
      ? undefined
      : "Invite Open SWE to this channel, then refresh to select it.",
  }))
  return (
    <OwnershipPicker
      triggerLabel="Choose channels"
      title="Slack channels"
      description="Mentions in these channels start runs in this workspace. A channel belongs to one workspace."
      noun="channel"
      pluralNoun="channels"
      items={items}
      selected={selected}
      workspaceSlug={workspaceSlug}
      onChange={(next) => {
        rememberSlackChannels(next.filter((id) => !selected.includes(id)))
        onChange(next)
      }}
      searchPlaceholder="Search channels"
      actions={
        <RefreshSlackChannels
          refresh={directory.refresh}
          isRefreshing={directory.isLoading || directory.isRefreshing}
        />
      }
      loading={directory.isLoading}
      loadError={
        directory.isError
          ? `Could not load channels from Slack${
              directory.error instanceof Error
                ? ` (${directory.error.message})`
                : ""
            }; refresh to try again.`
          : null
      }
      notice={
        directory.data?.refreshing
          ? "Refreshing channels from Slack. New ones appear when it finishes."
          : "Missing a channel? Invite Open SWE in Slack, then refresh this list."
      }
      disabled={disabled}
    />
  )
}
