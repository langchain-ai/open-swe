import { QuestionIcon } from "@phosphor-icons/react"

import { SlackChannelTextarea } from "@/components/SlackChannelTextarea"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { RepositoryPicker, SlackChannelPicker } from "./WorkspaceBindingPickers"
import { WorkspaceScriptEditor } from "./WorkspaceScriptEditor"
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
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => {
        const href = hrefFor?.(value) ?? null
        const className =
          "rounded-full border border-default px-2 py-0.5 text-[11px] text-secondary"
        if (!href)
          return (
            <span key={value} className={className}>
              {labelFor?.(value) ?? value}
            </span>
          )
        return (
          <a
            key={value}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className={`${className} hover:border-strong hover:text-primary`}
          >
            {labelFor?.(value) ?? value}
          </a>
        )
      })}
    </div>
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
    <ul className="w-full divide-y divide-default rounded-md border border-default">
      {draft.slackChannelIds.map((id) => (
        <li
          key={id}
          className="flex items-center justify-between gap-3 px-2.5 py-1.5"
        >
          <a
            href={slackChannelHref(id)}
            target="_blank"
            rel="noopener noreferrer"
            className="truncate text-xs hover:underline"
          >
            {channelLabel(id)}
          </a>
          <label className="flex shrink-0 items-center gap-2 text-xs text-secondary">
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
          </label>
        </li>
      ))}
    </ul>
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
    <div className="space-y-3 border-t border-default px-4 py-3.5">
      <label className="block text-sm">
        Name
        <Input
          aria-label="Workspace name"
          value={draft.name}
          onChange={(e) => onChange({ ...draft, name: e.target.value })}
        />
      </label>
      <div className="text-sm">
        <span className="inline-flex items-center gap-1.5">
          Bound repositories
          <Tooltip>
            <TooltipTrigger
              aria-label="About workspace repositories"
              className="rounded-full text-secondary transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--border-focus)]"
              type="button"
            >
              <QuestionIcon size={15} weight="fill" />
            </TooltipTrigger>
            <TooltipPopup className="max-w-72">
              Threads in any workspace can use any repository the GitHub App can
              access. Binding a repository routes its GitHub issues, pull
              requests, Linear tickets, and automations to this workspace and
              preloads it into this workspace&apos;s sandbox image. A repository
              is bound to one workspace.
            </TooltipPopup>
          </Tooltip>
        </span>
        <div
          role="group"
          aria-label="Bound repositories"
          className="mt-1 flex flex-wrap items-center gap-2"
        >
          {draft.repos.length > 0 ? (
            <Chips values={draft.repos} hrefFor={githubRepoHref} />
          ) : (
            <span className="text-xs text-secondary">None yet</span>
          )}
          <RepositoryPicker
            selected={draft.repos}
            onChange={(repos) => onChange({ ...draft, repos })}
            workspaceSlug={workspaceSlug}
            workspaces={workspaces}
          />
        </div>
      </div>
      <div className="text-sm">
        <span className="inline-flex items-center gap-1.5">
          Slack channels
          <Tooltip>
            <TooltipTrigger
              aria-label="About kitchen channels"
              className="rounded-full text-secondary transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--border-focus)]"
              type="button"
            >
              <QuestionIcon size={15} weight="fill" />
            </TooltipTrigger>
            <TooltipPopup className="max-w-72">
              Mentions in these channels start runs in this workspace. Turn on
              Kitchen for a channel to let every top-level message start a
              thread and replies continue it without mentioning Open SWE.
            </TooltipPopup>
          </Tooltip>
        </span>
        <div
          role="group"
          aria-label="Slack channels"
          className="mt-1 flex flex-wrap items-center gap-2"
        >
          {draft.slackChannelIds.length > 0 ? (
            <SlackChannelRows
              draft={draft}
              onChange={onChange}
              channelLabel={channelLabel}
            />
          ) : (
            <span className="text-xs text-secondary">
              None yet. Add a channel to turn on Kitchen mode for it.
            </span>
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
        </div>
      </div>
      <label className="block text-sm">
        Instructions
        <SlackChannelTextarea
          aria-label="Instructions"
          placeholder={promptHint}
          value={draft.prompt}
          onValueChange={(prompt) => onChange({ ...draft, prompt })}
        />
      </label>
      {workspaceSlug === null && (
        <>
          <div className="text-sm">
            <div>Setup script (optional)</div>
            <p className="text-xs text-secondary">
              Builds the image from the base snapshot immediately after creation
              and nightly. Without a setup script, no image is built.
            </p>
            <WorkspaceScriptEditor
              label="Setup script"
              repos={draft.repos}
              value={draft.setupScript}
              onChange={(setupScript) => onChange({ ...draft, setupScript })}
              description="Edit the shell script, then create the workspace to save it and start building the image."
            />
          </div>
          <div className="text-sm">
            <div>Update script (optional)</div>
            <p className="text-xs text-secondary">
              Runs after setup and refreshes the current image while it is in
              use.
            </p>
            <WorkspaceScriptEditor
              label="Update script"
              repos={draft.repos}
              value={draft.updateScript}
              onChange={(updateScript) => onChange({ ...draft, updateScript })}
              description="Edit the shell script, then create the workspace to save it."
            />
          </div>
        </>
      )}
    </div>
  )
}
