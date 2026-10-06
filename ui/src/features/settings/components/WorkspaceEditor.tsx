import { FormField } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import { SlackChannelTextarea } from "@/components/SlackChannelTextarea"
import { RepositoryPicker, SlackChannelPicker } from "./WorkspaceBindingPickers"
import { ScriptField, WorkspaceScriptEditor } from "./WorkspaceScriptEditor"
import { type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"
import { slackChannelHref } from "@/lib/slack-channels"

const REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
export const githubRepoHref: ChipHref = (repo) =>
  REPO_PATTERN.test(repo) ? `https://github.com/${repo}` : null

/** Turns a chip's value into a URL; a chip becomes a link when provided. */
export type ChipHref = (value: string) => string | null

export function Chips({
  values,
  hrefFor,
  labelFor,
}: {
  values: Array<string>
  hrefFor?: ChipHref
  labelFor?: (value: string) => string
}) {
  if (values.length === 0) return null
  return (
    <Inline gap="xs" wrap>
      {values.map((value) => {
        const href = hrefFor?.(value) ?? null
        const text = labelFor?.(value) ?? value
        if (!href)
          return (
            <Badge key={value} tier="quiet" tone="neutral">
              {text}
            </Badge>
          )
        return (
          <Badge
            key={value}
            tier="quiet"
            tone="neutral"
            className="hover:text-ink"
            render={<a href={href} target="_blank" rel="noopener noreferrer" />}
          >
            {text}
          </Badge>
        )
      })}
    </Inline>
  )
}

export interface WorkspaceDraft {
  name: string
  repos: Array<string>
  slackChannelIds: Array<string>
  kitchenChannelIds: Array<string>
  prompt: string
  setupScript: string
  updateScript: string
}

export function draftFromWorkspace(workspace: WorkspaceRecord): WorkspaceDraft {
  return {
    name: workspace.name,
    repos: workspace.repos,
    slackChannelIds: workspace.slack_channel_ids,
    kitchenChannelIds: workspace.kitchen_channel_ids,
    prompt: workspace.prompt,
    setupScript: "",
    updateScript: "",
  }
}

export const EMPTY_DRAFT: WorkspaceDraft = {
  name: "",
  repos: [],
  slackChannelIds: [],
  kitchenChannelIds: [],
  prompt: "",
  setupScript: "",
  updateScript: "",
}

function SlackChannelRows({
  draft,
  onChange,
  channelLabel,
}: {
  draft: WorkspaceDraft
  onChange: (next: WorkspaceDraft) => void
  channelLabel: (id: string) => string
}) {
  const kitchen = new Set(draft.kitchenChannelIds)
  return (
    <Stack
      render={<ul />}
      gap="none"
      border="line"
      radius="compact"
      className="w-full overflow-hidden"
    >
      {draft.slackChannelIds.map((id) => (
        <Inline
          render={<li />}
          key={id}
          gap="md"
          align="center"
          justify="between"
          className="border-b border-line px-3 py-1.5 last:border-b-0"
        >
          <Box
            render={
              <a
                href={slackChannelHref(id)}
                target="_blank"
                rel="noopener noreferrer"
              />
            }
            className="truncate text-label text-ink hover:underline"
          >
            {channelLabel(id)}
          </Box>
          <Inline
            render={<label />}
            gap="sm"
            align="center"
            className="shrink-0 text-meta text-ink-subtle"
          >
            Kitchen
            <Switch
              aria-label={`Kitchen mode for ${channelLabel(id)}`}
              checked={kitchen.has(id)}
              onCheckedChange={(on) => {
                if (on) kitchen.add(id)
                else kitchen.delete(id)
                onChange({ ...draft, kitchenChannelIds: [...kitchen].sort() })
              }}
            />
          </Inline>
        </Inline>
      ))}
    </Stack>
  )
}

export function WorkspaceEditor({
  draft,
  onChange,
  promptHint,
  workspaceSlug,
  workspaces,
  channelLabel,
}: {
  draft: WorkspaceDraft
  onChange: (next: WorkspaceDraft) => void
  promptHint: string
  /** The workspace being edited; null while creating one. */
  workspaceSlug: string | null
  workspaces: Array<WorkspaceOption>
  channelLabel: (id: string) => string
}) {
  return (
    <Stack gap="lg">
      <FormField
        label="Name"
        control={
          <Input
            aria-label="Workspace name"
            value={draft.name}
            onChange={(e) => onChange({ ...draft, name: e.target.value })}
          />
        }
      />
      <FormField
        label="Bound repositories"
        help="Threads in any workspace can use any repository the GitHub App can access. Binding a repository routes its GitHub issues, pull requests, Linear tickets, and automations to this workspace and preloads it into this workspace's sandbox image. A repository is bound to one workspace."
        control={
          <Inline
            role="group"
            aria-label="Bound repositories"
            gap="sm"
            align="center"
            wrap
          >
            {draft.repos.length > 0 ? (
              <Chips values={draft.repos} hrefFor={githubRepoHref} />
            ) : (
              <Box render={<span />} className="text-meta text-ink-subtle">
                None yet
              </Box>
            )}
            <RepositoryPicker
              selected={draft.repos}
              onChange={(repos) => onChange({ ...draft, repos })}
              workspaceSlug={workspaceSlug}
              workspaces={workspaces}
            />
          </Inline>
        }
      />
      <FormField
        label="Slack channels"
        help="Mentions in these channels start runs in this workspace. Turn on Kitchen for a channel to let every top-level message start a thread and replies continue it without mentioning Open SWE."
        control={
          <Inline
            role="group"
            aria-label="Slack channels"
            gap="sm"
            align="center"
            wrap
          >
            {draft.slackChannelIds.length > 0 ? (
              <SlackChannelRows
                draft={draft}
                onChange={onChange}
                channelLabel={channelLabel}
              />
            ) : (
              <Box render={<span />} className="text-meta text-ink-subtle">
                None yet. Add a channel to turn on Kitchen mode for it.
              </Box>
            )}
            <SlackChannelPicker
              selected={draft.slackChannelIds}
              onChange={(slackChannelIds) =>
                onChange({
                  ...draft,
                  slackChannelIds,
                  kitchenChannelIds: draft.kitchenChannelIds.filter((id) =>
                    slackChannelIds.includes(id)
                  ),
                })
              }
              workspaceSlug={workspaceSlug}
              workspaces={workspaces}
            />
          </Inline>
        }
      />
      <FormField
        label="Instructions"
        control={
          <SlackChannelTextarea
            aria-label="Instructions"
            placeholder={promptHint}
            value={draft.prompt}
            onValueChange={(prompt) => onChange({ ...draft, prompt })}
          />
        }
      />
      {workspaceSlug === null && (
        <>
          <ScriptField
            label="Setup script (optional)"
            help="Builds the image from the base snapshot immediately after creation and nightly. Without a setup script, no image is built."
          >
            <WorkspaceScriptEditor
              label="Setup script"
              repos={draft.repos}
              value={draft.setupScript}
              onChange={(setupScript) => onChange({ ...draft, setupScript })}
              description="Edit the shell script, then create the workspace to save it and start building the image."
            />
          </ScriptField>
          <ScriptField
            label="Update script (optional)"
            help="Runs after setup and refreshes the current image while it is in use."
          >
            <WorkspaceScriptEditor
              label="Update script"
              repos={draft.repos}
              value={draft.updateScript}
              onChange={(updateScript) => onChange({ ...draft, updateScript })}
              description="Edit the shell script, then create the workspace to save it."
            />
          </ScriptField>
        </>
      )}
    </Stack>
  )
}
