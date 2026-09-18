import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { RepositoryPicker, SlackChannelPicker } from "./WorkspaceBindingPickers"
import { type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"

export function Chips({ values }: { values: Array<string> }) {
  if (values.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <span
          key={value}
          className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
        >
          {value}
        </span>
      ))}
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
        Repositories
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
