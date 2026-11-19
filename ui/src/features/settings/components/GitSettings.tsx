import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api, type ProfileUpdate } from "@/lib/api"
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
  const { profile, ready } = useProfileSettings()
  return (
    <SettingsRow
      label={label}
      description={description}
      htmlFor={field}
      control={
        <TextSettingInput
          // Remount once the profile loads so the field shows the saved value.
          key={`${ready}`}
          field={field}
          saved={profile?.[field] ?? null}
          placeholder={placeholder}
        />
      }
    />
  )
}

function TextSettingInput({
  field,
  saved,
  placeholder,
}: {
  field: TextField
  saved: string | null
  placeholder: string
}) {
  const { ready, save } = useProfileSettings()
  const [draft, setDraft] = useState(saved ?? "")
  return (
    <Input
      id={field}
      size="md"
      className="w-56"
      placeholder={placeholder}
      value={draft}
      onChange={setDraft}
      disabled={!ready}
      onBlur={() => {
        const value = draft.trim() || null
        if (value !== saved)
          save({ [field]: value } satisfies Partial<ProfileUpdate>)
      }}
    />
  )
}

export function GitSettings() {
  const { profile, ready, save } = useProfileSettings()
  const repos = useRepos()
  const settings = useQuery({
    queryKey: ["instanceSettings"],
    queryFn: api.getInstanceSettings,
  })
  const teamDefaultOn = settings.data?.review_draft_prs ?? false
  const expeditedOn = settings.data?.expedited_review_enabled ?? false
  const draftReviewItems: Array<{ value: DraftReviewChoice; label: string }> = [
    {
      value: "team_default",
      label: `Default (${teamDefaultOn ? "on" : "off"})`,
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
          label="Default Repository"
          description="Used when a request does not name a repository."
          control={
            <div className="w-56">
              <RepoSelector
                repos={repos.data?.repositories ?? []}
                selectedRepo={profile?.default_repo ?? null}
                onRepoChange={(repo) => save({ default_repo: repo })}
                placeholder="Pick a repository…"
                emptySelectionLabel="No default repository"
                disabled={!ready}
                triggerClassName="h-7 w-full max-w-none rounded-md border border-default bg-surface-level-1 px-space-2 py-space-1 text-xs text-primary transition-colors hover:bg-surface-level-1-hover"
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
          placeholder="robin/"
        />
      </SettingsSection>

      <SettingsSection
        title="Pull Requests"
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
          description="Whether Robin Review runs on draft pull requests you open."
          control={
            <Select
              aria-label="Review my drafts"
              options={draftReviewItems}
              value={toChoice(profile?.review_draft_prs)}
              onChange={(v) => v && save({ review_draft_prs: CHOICES[v] })}
              disabled={!ready}
              triggerClassName="w-48"
            />
          }
        />
      </SettingsSection>
    </>
  )
}
