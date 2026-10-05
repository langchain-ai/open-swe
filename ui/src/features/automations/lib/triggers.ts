import type {
  AgentSchedule,
  AutomationTriggerConfig,
  GitHubTriggerEvent,
} from "@/features/agents/lib/types"
import { AUTOMATION_EVENT_PROVIDERS } from "@/features/agents/lib/types"
import {
  describeCron,
  isDescribableCron,
} from "@/features/automations/lib/cron"

/** A trigger being edited: a GitHub one may not have its repository yet. */
export type TriggerDraft =
  | {
      key: string
      kind: "schedule"
      cron: string
      repo: string | null
      /** Edit the cron as text rather than through a preset. */
      custom: boolean
    }
  | {
      key: string
      kind: "github"
      repo: string | null
      events: Array<GitHubTriggerEvent>
    }

let nextKey = 0
const draftKey = () => `trigger-${++nextKey}`

export function scheduleDraft(cron: string, repo: string | null = null) {
  return {
    key: draftKey(),
    kind: "schedule" as const,
    cron,
    repo,
    custom: !isDescribableCron(cron),
  }
}

export function githubDraft(
  repo: string | null = null,
  events: Array<GitHubTriggerEvent> = []
) {
  return { key: draftKey(), kind: "github" as const, repo, events }
}

export function draftsFor(
  schedule?: AgentSchedule,
  templateCron?: string | null
): Array<TriggerDraft> {
  if (!schedule) return templateCron ? [scheduleDraft(templateCron)] : []
  return schedule.triggers.map((trigger) =>
    trigger.kind === "schedule"
      ? scheduleDraft(trigger.cron, trigger.repo ?? null)
      : githubDraft(trigger.repo, trigger.events)
  )
}

/** Why a draft cannot be saved yet, or null when it can. */
export function draftProblem(draft: TriggerDraft): string | null {
  if (draft.kind === "schedule") {
    return draft.cron.trim() ? null : "Enter a cron schedule."
  }
  if (!draft.repo) return "Pick the repository whose events run this."
  if (draft.events.length === 0) return "Pick at least one event."
  return null
}

export function toTriggers(
  drafts: Array<TriggerDraft>
): Array<AutomationTriggerConfig> {
  return drafts.flatMap((draft): Array<AutomationTriggerConfig> => {
    if (draftProblem(draft)) return []
    if (draft.kind === "schedule") {
      const cron = draft.cron.trim()
      return [
        draft.repo
          ? { kind: "schedule", cron, repo: draft.repo }
          : { kind: "schedule", cron },
      ]
    }
    return draft.repo
      ? [{ kind: "github", repo: draft.repo, events: draft.events }]
      : []
  })
}

/** One part per trigger, e.g. "GitHub acme/oss: PR closed, PR merged". */
export function describeTriggers(schedule: AgentSchedule): string {
  const parts = schedule.triggers.map((trigger) => {
    if (trigger.kind === "schedule") {
      const when = describeCron(trigger.cron)
      return trigger.repo ? `${when} in ${trigger.repo}` : when
    }
    const provider = AUTOMATION_EVENT_PROVIDERS[trigger.kind]
    const events = trigger.events.map((event) => provider.events[event])
    return `${provider.label} ${trigger.repo}: ${events.join(", ")}`
  })
  return parts.length > 0 ? parts.join(" · ") : "No trigger"
}
