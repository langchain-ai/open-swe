import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"

import {
  RefreshSlackChannels,
  SlackChannelIcon,
} from "@/components/SlackChannelCombobox"
import { RepositoryLabels } from "@/lib/repo"
import { useRepos } from "@/lib/profile"
import {
  rankSlackChannels,
  slackChannelLabel,
  rememberSlackChannels,
  useRecentSlackChannels,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
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

export function SlackChannelPicker({
  selected,
  onChange,
  disabled,
}: BindingPickerProps) {
  const directory = useSlackChannelDirectory(true)
  const recent = useRecentSlackChannels()
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
    disabled: !channel.is_member,
    warning: channel.is_member
      ? undefined
      : "Invite Robin to this channel, then refresh to select it.",
  }))
  for (const id of selected) {
    if (!items.some((item) => item.id === id))
      items.push({ id, label: slackChannelLabel(directory.data, id, selected) })
  }
  return (
    <BindingPicker
      triggerLabel="Choose channels"
      title="Slack channels"
      description="Bind channels where Robin responds to mentions. Set Mention-only, Kitchen, or Ask after saving."
      noun="channel"
      pluralNoun="channels"
      items={items}
      selected={selected}
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
          : "Missing a channel? Invite Robin in Slack, then refresh this list."
      }
      disabled={disabled}
    />
  )
}
