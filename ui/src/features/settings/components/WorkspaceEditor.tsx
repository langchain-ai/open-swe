import { QuestionIcon } from "@phosphor-icons/react"

import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { RepositoryPicker, SlackChannelPicker } from "./WorkspaceBindingPickers"
import { type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"

/** Turns a chip's value into a URL; a chip becomes a link when provided. */
export type ChipHref = (value: string) => string | null

export function Chips({
  values,
  hrefFor,
}: {
  values: Array<string>
  hrefFor?: ChipHref
}) {
  if (values.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => {
        const href = hrefFor?.(value) ?? null
        const className =
          "rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
        if (!href)
          return (
            <span key={value} className={className}>
              {value}
            </span>
          )
        return (
          <a
            key={value}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className={`${className} hover:border-foreground/30 hover:text-foreground`}
          >
            {value}
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
  prompt: string
}

export function draftFromWorkspace(workspace: WorkspaceRecord): WorkspaceDraft {
  return {
    name: workspace.name,
    repos: workspace.repos,
    slackChannelIds: workspace.slack_channel_ids,
    prompt: workspace.prompt,
  }
}

export const EMPTY_DRAFT: WorkspaceDraft = {
  name: "",
  repos: [],
  slackChannelIds: [],
  prompt: "",
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
    <div className="space-y-3 border-t border-border px-4 py-3.5">
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
          Repositories
          <Tooltip>
            <TooltipTrigger
              aria-label="About workspace repositories"
              className="rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              type="button"
            >
              <QuestionIcon size={15} weight="fill" />
            </TooltipTrigger>
            <TooltipPopup className="max-w-72">
              Assigning a repository makes this the workspace for requests
              targeting that repository, whether submitted from the dashboard,
              Slack, GitHub issues, or pull requests. Runs use this
              workspace&apos;s sandbox, instructions, settings, and connections.
              Each repository can belong to only one workspace.
            </TooltipPopup>
          </Tooltip>
        </span>
        <div
          role="group"
          aria-label="Repositories"
          className="mt-1 flex flex-wrap items-center gap-2"
        >
          {draft.repos.length > 0 ? (
            <Chips values={draft.repos} />
          ) : (
            <span className="text-xs text-muted-foreground">None yet</span>
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
        Slack channels
        <div
          role="group"
          aria-label="Slack channels"
          className="mt-1 flex flex-wrap items-center gap-2"
        >
          {draft.slackChannelIds.length > 0 ? (
            <Chips values={draft.slackChannelIds.map(channelLabel)} />
          ) : (
            <span className="text-xs text-muted-foreground">None yet</span>
          )}
          <SlackChannelPicker
            selected={draft.slackChannelIds}
            onChange={(slackChannelIds) =>
              onChange({ ...draft, slackChannelIds })
            }
            workspaceSlug={workspaceSlug}
            workspaces={workspaces}
          />
        </div>
      </div>
      <label className="block text-sm">
        Instructions
        <Textarea
          aria-label="Instructions"
          placeholder={promptHint}
          value={draft.prompt}
          onChange={(e) => onChange({ ...draft, prompt: e.target.value })}
        />
      </label>
    </div>
  )
}
