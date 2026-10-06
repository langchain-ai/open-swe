import { useQuery } from "@tanstack/react-query"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@langchain/gtm-platform-design-system/ui/select"
import { api, DEFAULT_WORKSPACE_SLUG, type ProfileUpdate } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { ProfileSwitchRow, useProfileSettings } from "./ProfileSwitchRow"
import { RepoSelector } from "./RepoSelector"

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

type TextField = "base_branch" | "branch_prefix"

function TextSettingRow({
  field,
  label,
  description,
  placeholder,
}: {
  field: TextField
  label: string
  description: string
  placeholder: string
}) {
  const { profile, ready, save } = useProfileSettings()
  return (
    <SettingsRow
      label={label}
      description={description}
      htmlFor={field}
      control={
        <Input
          // Remount once the profile loads so the field shows the saved value.
          key={`${ready}`}
          id={field}
          className="w-56"
          placeholder={placeholder}
          defaultValue={profile?.[field] ?? ""}
          disabled={!ready}
          onBlur={(event) => {
            const value = event.target.value.trim() || null
            if (value !== (profile?.[field] ?? null))
              save({ [field]: value } satisfies Partial<ProfileUpdate>)
          }}
        />
      }
    />
  )
}

export function GitSettings() {
  const { profile, ready, save } = useProfileSettings()
  const repos = useRepos()
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

  const teamDefaultOn =
    workspaceSettings.data?.effective.review_draft_prs ?? false
  const expeditedOn =
    workspaceSettings.data?.effective.expedited_review_enabled ?? false
  const draftReviewItems: Array<{ value: DraftReviewChoice; label: string }> = [
    {
      value: "team_default",
      label: `Workspace default (${teamDefaultOn ? "on" : "off"})`,
    },
    { value: "always_on", label: "Always" },
    { value: "always_off", label: "Never" },
  ]

  return (
    <>
      <SettingsSection
        title="Repository"
        description="Where runs work when a request doesn't name a repository or branch."
      >
        <SettingsRow
          label="Default repository"
          description="Used when a request doesn't name a repository."
          control={
            <div className="w-56">
              <RepoSelector
                repos={repos.data?.repositories ?? []}
                selectedRepo={profile?.default_repo ?? null}
                onRepoChange={(repo) => save({ default_repo: repo })}
                placeholder="Pick a repository…"
                emptySelectionLabel="No default repository"
                disabled={!ready}
                triggerClassName="h-7 w-full max-w-none rounded-badge border border-line-strong bg-line-strong/20 px-2 py-1.5 text-label text-ink transition-colors hover:opacity-100"
                dropdownClassName="w-56"
              />
            </div>
          }
        />
        <TextSettingRow
          field="base_branch"
          label="Base branch"
          description="Leave empty to use each repository's default branch (recommended)."
          placeholder="Repository default"
        />
        <TextSettingRow
          field="branch_prefix"
          label="Branch prefix"
          description="Prefix for branches the agent creates."
          placeholder="open-swe/"
        />
      </SettingsSection>

      <SettingsSection
        title="Pull requests"
        description="How pull requests you trigger are opened and reviewed."
      >
        <ProfileSwitchRow
          field="draft_prs"
          label="Open as draft"
          description={
            expeditedOn
              ? "New pull requests start as drafts, except ones the agent nominates for expedited Slack review."
              : "New pull requests start as drafts. Existing ones keep their status."
          }
          fallback
        />
        <SettingsRow
          label="Review my drafts"
          description="Whether Open SWE Review runs on draft pull requests you open."
          control={
            <Select
              items={draftReviewItems}
              value={toChoice(profile?.review_draft_prs)}
              onValueChange={(v) =>
                save({ review_draft_prs: CHOICES[v as DraftReviewChoice] })
              }
              disabled={!ready}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {draftReviewItems.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />
      </SettingsSection>
    </>
  )
}
