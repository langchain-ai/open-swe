import { Badge } from "@langchain/macaw-components/Badge"
import { Select } from "@langchain/macaw-components/Select"

import { SettingsSection } from "@/components/AppShell"
import type { ModelOption } from "@/lib/api"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { ModelPairControl, TierRow } from "./WorkspaceSettingsSections"

const PERCENTS = [0, 1, 2, 5, 10, 25, 50, 100]

export function ShadowReviewSection({
  scope,
  models,
}: {
  scope: SettingsScope
  models: Array<ModelOption>
}) {
  const settings = useScopedSettings(scope)
  const percent = settings.data?.review_shadow_percent ?? 0
  // A share set through the agent tool may be none of the presets.
  const percentOptions = [...new Set([...PERCENTS, percent])]
    .sort((a, b) => a - b)
    .map((value) => ({
      value: String(value),
      label: value === 0 ? "Off" : `${value}%`,
    }))
  return (
    <SettingsSection
      title={
        <span className="inline-flex items-center gap-space-2">
          Shadow reviews
          <Badge color="secondary" size="xs">
            Experimental
          </Badge>
        </span>
      }
      description="Runs a second reviewer on another model for a sample of pull requests, to compare what each finds. A sampled pull request's shadow follows its every review with the same prompt and context, but never posts to GitHub or Slack; its findings are stored for offline comparison."
    >
      <div className="divide-y divide-default">
        <TierRow
          settings={settings}
          fields={["review_shadow_percent"]}
          label="Pull requests sampled"
          description="Share of pull requests sampled when their first review starts. Pull requests already sampled keep their shadow."
          control={
            <Select
              aria-label="Pull requests sampled"
              options={percentOptions}
              value={String(percent)}
              onChange={(next) => {
                if (next) settings.save({ review_shadow_percent: Number(next) })
              }}
              disabled={!settings.data}
              triggerClassName="w-28"
            />
          }
        />
        <TierRow
          settings={settings}
          fields={["review_shadow_model", "review_shadow_reasoning_effort"]}
          label="Shadow model"
          description="Model the shadow reviewer runs on. Sampling stays off until it is set; a sampled pull request keeps the model it was sampled with."
          control={
            <ModelPairControl
              label="Shadow reviewer"
              models={models}
              model={settings.data?.review_shadow_model ?? null}
              effort={settings.data?.review_shadow_reasoning_effort ?? null}
              onChange={(model, effort) =>
                settings.save({
                  review_shadow_model: model,
                  review_shadow_reasoning_effort: effort,
                })
              }
              disabled={!settings.data}
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
