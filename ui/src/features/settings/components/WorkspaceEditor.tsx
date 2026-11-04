import { Link as MacawLink } from "@langchain/macaw-components/Link"
import type { ReactNode } from "react"
import { Input } from "@langchain/macaw-components/Input"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Select } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"
import { QuestionIcon } from "@phosphor-icons/react/dist/ssr/Question"

import { SlackChannelTextarea } from "@/components/SlackChannelTextarea"
import { RepositoryPicker, SlackChannelPicker } from "./WorkspaceBindingPickers"
import { WorkspaceScriptEditor } from "./WorkspaceScriptEditor"
import { type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"
import {
  slackChannelHref,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { cn, INHERITED_LINK } from "@/lib/utils"

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
    <div className="flex flex-wrap gap-space-2">
      {values.map((value) => {
        const href = hrefFor?.(value) ?? null
        const className =
          "rounded-full border border-default px-space-2 py-0.5 text-xxs text-secondary"
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
            className={`${className} hover:bg-surface-level-1-hover hover:text-primary`}
          >
            {labelFor?.(value) ?? value}
          </a>
        )
      })}
    </div>
  )
}

/** A question-mark button that explains the field next to it. */
export function HelpTooltip({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <IconButton
      icon={QuestionIcon}
      iconWeight="fill"
      label={label}
      size="xs"
      variant="plain"
      color="secondary"
      round
      tooltipProps={{ title: children, tooltipClassName: "max-w-72" }}
    />
  )
}

export interface WorkspaceDraft {
  name: string
  repos: Array<string>
  slackChannelIds: Array<string>
  kitchenChannelIds: Array<string>
  /** Maps a bound non-kitchen channel to the kitchen its bug reports start threads in. */
  bugReportKitchens: Record<string, string>
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
    bugReportKitchens: workspace.bug_report_kitchens ?? {},
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
  bugReportKitchens: {},
  prompt: "",
  setupScript: "",
  updateScript: "",
}

const BUG_REPORTS_OFF = "off"

function SlackChannelRows({
  draft,
  onChange,
  channelLabel,
}: {
  draft: WorkspaceDraft
  onChange: (next: WorkspaceDraft) => void
  channelLabel: (id: string) => string
}) {
  const directory = useSlackChannelDirectory(true).data
  // A route copies messages between channels, so the server takes it only between
  // public channels Open SWE has joined; one the directory doesn't list is left to it.
  const routable = (id: string) => {
    const channel = directory?.channels.find((entry) => entry.id === id)
    return (
      !channel ||
      (!channel.is_private && channel.is_member && !channel.is_ext_shared)
    )
  }
  const kitchen = new Set(draft.kitchenChannelIds)
  const routableKitchens = draft.kitchenChannelIds.filter(routable)
  const bugReportOptions = (id: string) => {
    const current = draft.bugReportKitchens[id]
    return [
      { value: BUG_REPORTS_OFF, label: "Off" },
      ...draft.kitchenChannelIds
        .filter((kitchenId) => kitchenId === current || routable(kitchenId))
        .map((kitchenId) => ({
          value: kitchenId,
          label: channelLabel(kitchenId),
        })),
    ]
  }
  // A route already set stays editable, so it can always be turned off.
  const routesBugReports = (id: string) =>
    !kitchen.has(id) &&
    (id in draft.bugReportKitchens ||
      (routable(id) && routableKitchens.length > 0))
  return (
    <ul className="w-full divide-y divide-default rounded-md border border-default">
      {draft.slackChannelIds.map((id) => (
        <li
          key={id}
          className="flex flex-wrap items-center justify-between gap-x-space-3 gap-y-space-1 px-space-2 py-space-1"
        >
          <MacawLink
            variant="xs"
            href={slackChannelHref(id)}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              INHERITED_LINK,
              "min-w-0 truncate text-xs text-primary hover:underline [&>span]:truncate"
            )}
          >
            {channelLabel(id)}
          </MacawLink>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-x-space-3 gap-y-space-1">
            {routesBugReports(id) && (
              <label className="flex items-center gap-space-1 text-xs text-secondary">
                Bug reports
                <Select
                  variant="plain"
                  size="xs"
                  align="end"
                  aria-label={`Bug reports for ${channelLabel(id)}`}
                  options={bugReportOptions(id)}
                  value={draft.bugReportKitchens[id] ?? BUG_REPORTS_OFF}
                  onChange={(target) => {
                    const routes = new Map(
                      Object.entries(draft.bugReportKitchens)
                    )
                    if (target && target !== BUG_REPORTS_OFF)
                      routes.set(id, target)
                    else routes.delete(id)
                    onChange({
                      ...draft,
                      bugReportKitchens: Object.fromEntries(routes),
                    })
                  }}
                  triggerClassName="max-w-48"
                />
              </label>
            )}
            <label className="flex shrink-0 items-center gap-space-2 text-xs text-secondary">
              Kitchen
              <Switch
                aria-label={`Kitchen mode for ${channelLabel(id)}`}
                checked={kitchen.has(id)}
                onChange={(on) => {
                  if (on) kitchen.add(id)
                  else kitchen.delete(id)
                  onChange({ ...draft, kitchenChannelIds: [...kitchen].sort() })
                }}
              />
            </label>
          </div>
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
  const onChannelsChange = (next: WorkspaceDraft) => {
    const kitchens = next.kitchenChannelIds.filter((id) =>
      next.slackChannelIds.includes(id)
    )
    onChange({
      ...next,
      kitchenChannelIds: kitchens,
      bugReportKitchens: Object.fromEntries(
        next.slackChannelIds.flatMap((id) => {
          const kitchen = next.bugReportKitchens[id]
          return kitchen && kitchens.includes(kitchen) && !kitchens.includes(id)
            ? [[id, kitchen] as const]
            : []
        })
      ),
    })
  }
  return (
    <div className="space-y-space-3 border-t border-default px-space-4 py-space-3 text-primary">
      <Input
        label="Name"
        aria-label="Workspace name"
        size="md"
        value={draft.name}
        onChange={(name) => onChange({ ...draft, name })}
      />
      <div className="text-sm">
        <span className="inline-flex items-center gap-space-2">
          Bound repositories
          <HelpTooltip label="About workspace repositories">
            Threads in any workspace can use any repository the GitHub App can
            access. Binding a repository routes its GitHub issues, pull
            requests, Linear tickets, and automations to this workspace and
            preloads it into this workspace&apos;s sandbox image. A repository
            is bound to one workspace.
          </HelpTooltip>
        </span>
        <div
          role="group"
          aria-label="Bound Repositories"
          className="mt-space-1 flex flex-wrap items-center gap-space-2"
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
        <span className="inline-flex items-center gap-space-2">
          Slack channels
          <HelpTooltip label="About kitchen channels">
            Mentions in these channels start runs in this workspace. Turn on
            Kitchen for a channel to let every top-level message start a thread
            and replies continue it without mentioning Open SWE. For any other
            public channel, pick a public kitchen under Bug reports: Open SWE
            reads each new top-level message there and starts a thread in that
            kitchen for every bug report.
          </HelpTooltip>
        </span>
        <div
          role="group"
          aria-label="Slack channels"
          className="mt-space-1 flex flex-wrap items-center gap-space-2"
        >
          {draft.slackChannelIds.length > 0 ? (
            <SlackChannelRows
              draft={draft}
              onChange={onChannelsChange}
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
              onChannelsChange({ ...draft, slackChannelIds })
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
