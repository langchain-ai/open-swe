import { Select } from "@langchain/macaw-components/Select"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import type { SandboxGitHubBudgetMode } from "@/lib/api"
import { useInstanceSettings } from "@/features/settings/lib/settingsScope"

const MODES: Array<{ value: SandboxGitHubBudgetMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "observe", label: "Observe" },
  { value: "enforce", label: "Enforce" },
]
const PER_HOUR = [100, 250, 500, 1000, 2000, 5000]
const BRAKE_PERCENTS = [0, 5, 10, 20, 30, 50]

/** Instance only: every workspace's sandboxes share the App installation's one quota. */
export function SandboxGitHubBudgetSection() {
  const settings = useInstanceSettings()
  const mode = settings.data?.sandbox_github_budget_mode ?? "observe"
  const perHour = settings.data?.sandbox_github_budget_per_hour ?? 500
  const brake = settings.data?.sandbox_github_brake_percent ?? 20
  // The agent tool may save a value that is none of the presets.
  const perHourOptions = [...new Set([...PER_HOUR, perHour])]
    .sort((a, b) => a - b)
    .map((value) => ({ value: String(value), label: String(value) }))
  const brakeOptions = [...new Set([...BRAKE_PERCENTS, brake])]
    .sort((a, b) => a - b)
    .map((value) => ({ value: String(value), label: `${value}%` }))
  return (
    <SettingsSection
      title="Sandbox GitHub API budget"
      description="Every sandbox using Robin's GitHub App token shares one hourly GitHub API quota with Robin's own reviews and approvals. Enforce caps each thread's share and pauses sandbox calls when the quota runs low; Observe, the default, only counts calls, to show real usage before anything is refused. git clone, pull and push are never limited."
    >
      <div className="divide-y divide-default">
        <SettingsRow
          label="Mode"
          description="Observe counts and logs every call without refusing any; Enforce also refuses calls over the limits. Both check with Robin's backend on every call instead of every 5 minutes. Sandboxes pick up a change at their thread's next run."
          control={
            <Select
              aria-label="Sandbox GitHub API budget mode"
              options={MODES}
              value={mode}
              onChange={(next) => {
                const picked = MODES.find((option) => option.value === next)
                if (picked)
                  settings.save({ sandbox_github_budget_mode: picked.value })
              }}
              disabled={!settings.data}
              triggerClassName="w-28"
            />
          }
        />
        <SettingsRow
          label="Calls per thread per hour"
          description="GitHub API calls one thread's sandbox may make in any 60 minutes before Enforce refuses more."
          control={
            <Select
              aria-label="Calls per thread per hour"
              options={perHourOptions}
              value={String(perHour)}
              onChange={(next) => {
                if (next)
                  settings.save({
                    sandbox_github_budget_per_hour: Number(next),
                  })
              }}
              disabled={!settings.data}
              triggerClassName="w-28"
            />
          }
        />
        <SettingsRow
          label="Brake"
          description="In Enforce, while less than this share of the hourly quota is left, every sandbox call is refused until GitHub resets it, keeping the rest for reviews and approvals. 0% turns the brake off."
          control={
            <Select
              aria-label="Brake"
              options={brakeOptions}
              value={String(brake)}
              onChange={(next) => {
                if (next)
                  settings.save({ sandbox_github_brake_percent: Number(next) })
              }}
              disabled={!settings.data}
              triggerClassName="w-28"
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
