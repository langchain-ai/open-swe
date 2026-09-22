import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { api, DEFAULT_WORKSPACE_SLUG } from "@/lib/api"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useSaveProfile,
} from "@/lib/profile"

type DraftReviewChoice = "team_default" | "always_on" | "always_off"

const CHOICES: Record<DraftReviewChoice, boolean | null> = {
  team_default: null,
  always_on: true,
  always_off: false,
}

function toChoice(value: boolean | null | undefined): DraftReviewChoice {
  if (value === true) return "always_on"
  if (value === false) return "always_off"
  return "team_default"
}

export function PullRequestsSection() {
  const profile = useProfile()
  const options = useOptions()
  const save = useSaveProfile()
  // Which team default applies depends on the workspace a pull request's
  // repository belongs to; the user's own workspace is the one answer this
  // page can give, and it is where their new threads run.
  const preferences = useQuery({
    queryKey: ["myPreferences"],
    queryFn: api.getMyPreferences,
  })
  const workspace =
    preferences.data?.default_workspace ?? DEFAULT_WORKSPACE_SLUG
  const workspaceSettings = useQuery({
    queryKey: ["workspaceSettings", workspace],
    queryFn: () => api.getWorkspaceSettings(workspace),
  })
  const [error, setError] = useState<string | null>(null)

  const firstModel = options.data?.models[0]
  const fallbackModel =
    options.data?.default_agent_model ?? firstModel?.id ?? ""
  const fallbackEffort =
    options.data?.default_agent_reasoning_effort ??
    firstModel?.default_effort ??
    ""

  const persist = (patch: Parameters<typeof buildProfileUpdate>[1]) => {
    setError(null)
    save
      .mutateAsync(
        buildProfileUpdate(profile.data, patch, fallbackModel, fallbackEffort)
      )
      .catch((e: Error) => setError(e.message))
  }

  const disabled = profile.isLoading || save.isPending
  const teamDefaultOn =
    workspaceSettings.data?.effective.review_draft_prs ?? false
  const expeditedOn =
    workspaceSettings.data?.effective.expedited_review_enabled ?? false

  return (
    <SettingsSection
      title="Pull requests"
      description="How pull requests you trigger are opened and reviewed."
    >
      <SettingsRow
        label="Create PRs as draft"
        description={
          expeditedOn
            ? "New pull requests are created as drafts, except ones the agent nominates for expedited Slack review, which it marks ready. Existing pull requests keep their current draft status."
            : "New pull requests are created as drafts. Existing pull requests keep their current draft status."
        }
        control={
          <Switch
            checked={profile.data?.draft_prs ?? true}
            onCheckedChange={(v) => persist({ draft_prs: v })}
            disabled={disabled}
          />
        }
      />
      <SettingsRow
        label="Review my draft PRs"
        description="Whether Open SWE Review runs on pull requests you open in draft."
        control={
          <Select
            value={toChoice(profile.data?.review_draft_prs)}
            onValueChange={(v) =>
              persist({ review_draft_prs: CHOICES[v as DraftReviewChoice] })
            }
            disabled={disabled}
          >
            <SelectTrigger className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="team_default">
                {`Use workspace default (currently: ${
                  teamDefaultOn ? "On" : "Off"
                })`}
              </SelectItem>
              <SelectItem value="always_on">Always review my drafts</SelectItem>
              <SelectItem value="always_off">Never review my drafts</SelectItem>
            </SelectContent>
          </Select>
        }
      />
      {error && <p className="px-4 py-2 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
