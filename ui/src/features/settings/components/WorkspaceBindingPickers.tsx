import { GlobeRegularIcon } from "@langchain/macaw-components/icons"
import { useMemo } from "react"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { HashIcon } from "@phosphor-icons/react/dist/ssr/Hash"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"

import { RefreshSlackChannels } from "@/components/SlackChannelCombobox"
import { type WorkspaceOption } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { useSlackChannelDirectory } from "@/lib/slack-channels"
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
  const reposByName = useMemo(
    () =>
      new Map(
        (repos.data?.repositories ?? []).map((repo) => [repo.full_name, repo])
      ),
    [repos.data]
  )
  const owners = useMemo(
    () => ownersOf(workspaces, (workspace) => workspace.repos),
    [workspaces]
  )
  const items = useMemo<Array<PickerItem>>(
    () =>
      (repos.data?.repositories ?? []).map((repo) => ({
        id: repo.full_name,
        label: repo.full_name,
        meta: `${repo.private ? "Private" : "Public"}${repo.archived ? " archive" : ""}`,
        icon: repo.private ? (
          <LockSimpleIcon size={14} weight="regular" />
        ) : (
          <FolderIcon size={14} weight="regular" />
        ),
        owner: owners.get(repo.full_name.toLowerCase()) ?? null,
      })),
    [repos.data, owners]
  )
  return (
    <OwnershipPicker
      triggerLabel="Choose repositories"
      title="Bound repositories"
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
  const owners = useMemo(
    () => ownersOf(workspaces, (workspace) => workspace.slack_channel_ids),
    [workspaces]
  )
  const items = useMemo<Array<PickerItem>>(
    () =>
      (directory.data?.channels ?? []).map((channel) => ({
        id: channel.id,
        label: `#${channel.name}`,
        meta: [
          channel.is_private
            ? "private"
            : channel.is_ext_shared
              ? "shared"
              : "public",
          channel.num_members === null
            ? null
            : `${channel.num_members} members`,
        ]
          .filter((part): part is string => part !== null)
          .join(" · "),
        icon: channel.is_private ? (
          <LockSimpleIcon size={14} weight="regular" />
        ) : channel.is_ext_shared ? (
          <GlobeRegularIcon size={14} />
        ) : (
          <HashIcon size={14} weight="regular" />
        ),
        owner: owners.get(channel.id.toLowerCase()) ?? null,
        disabled: !channel.is_member,
        warning: channel.is_member
          ? undefined
          : "Invite Open SWE to this channel, then refresh to select it.",
      })),
    [directory.data, owners]
  )
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
      onChange={onChange}
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
        directory.data?.partial
          ? "Slack is rate limiting the full directory, so only channels the bot is in are listed."
          : "Missing a channel? Invite Open SWE in Slack, then refresh this list."
      }
      disabled={disabled}
    />
  )
}
