import type {
  AgentSchedule,
  AutomationTriggerConfig,
  GitHubTriggerEvent,
  LinearTriggerEvent,
  SlackTriggerEvent,
  SlackTriggerSenders,
} from "@/features/agents/lib/types"
import { AUTOMATION_EVENT_PROVIDERS } from "@/features/agents/lib/types"
import {
  describeCron,
  isDescribableCron,
} from "@/features/automations/lib/cron"

/** A trigger being edited: its required fields may still be empty. */
export type TriggerDraft =
  | {
      key: string
      kind: "schedule"
      cron: string
      /** Edit the cron as text rather than through a preset. */
      custom: boolean
    }
  | {
      key: string
      kind: "github"
      repo: string | null
      events: Array<GitHubTriggerEvent>
    }
  | {
      key: string
      kind: "slack"
      channel: string | null
      events: Array<SlackTriggerEvent>
      senders: SlackTriggerSenders
      match: string
      maxRunsPerHour: string
    }
  | {
      key: string
      kind: "linear"
      team: string
      events: Array<LinearTriggerEvent>
      labels: string
      project: string
      maxRunsPerHour: string
    }

let nextKey = 0
const draftKey = () => `trigger-${++nextKey}`

export function scheduleDraft(cron: string) {
  return {
    key: draftKey(),
    kind: "schedule" as const,
    cron,
    custom: !isDescribableCron(cron),
  }
}

export function githubDraft(
  repo: string | null = null,
  events: Array<GitHubTriggerEvent> = []
) {
  return { key: draftKey(), kind: "github" as const, repo, events }
}

export function slackDraft(
  trigger?: Extract<AutomationTriggerConfig, { kind: "slack" }>
) {
  return {
    key: draftKey(),
    kind: "slack" as const,
    channel: trigger?.channel ?? null,
    events: trigger?.events ?? (["message.posted"] as Array<SlackTriggerEvent>),
    senders: trigger?.senders ?? ("anyone" as SlackTriggerSenders),
    match: trigger?.match ?? "",
    maxRunsPerHour: trigger?.max_runs_per_hour?.toString() ?? "",
  }
}

export function linearDraft(
  trigger?: Extract<AutomationTriggerConfig, { kind: "linear" }>
) {
  return {
    key: draftKey(),
    kind: "linear" as const,
    team: trigger?.team ?? "",
    events: trigger?.events ?? [],
    labels: trigger?.labels?.join(", ") ?? "",
    project: trigger?.project ?? "",
    maxRunsPerHour: trigger?.max_runs_per_hour?.toString() ?? "",
  }
}

export function draftsFor(
  schedule?: AgentSchedule,
  templateCron?: string | null
): Array<TriggerDraft> {
  if (!schedule) return templateCron ? [scheduleDraft(templateCron)] : []
  return schedule.triggers.map((trigger): TriggerDraft => {
    switch (trigger.kind) {
      case "schedule":
        return scheduleDraft(trigger.cron)
      case "github":
        return githubDraft(trigger.repo, trigger.events)
      case "slack":
        return slackDraft(trigger)
      case "linear":
        return linearDraft(trigger)
    }
  })
}

function runsPerHour(value: string): number | null {
  return value.trim() ? Number(value) : null
}

/** Why a draft cannot be saved yet, or null when it can. */
export function draftProblem(draft: TriggerDraft): string | null {
  switch (draft.kind) {
    case "schedule":
      return draft.cron.trim() ? null : "Enter a cron schedule."
    case "github":
      if (!draft.repo) return "Pick the repository whose events run this."
      return draft.events.length === 0 ? "Pick at least one event." : null
    case "slack":
    case "linear": {
      if (draft.kind === "slack" && !draft.channel) {
        return "Pick the channel whose messages run this."
      }
      if (
        draft.kind === "linear" &&
        !/^[A-Za-z][A-Za-z0-9_]{0,9}$/.test(draft.team.trim())
      ) {
        return "Enter the Linear team key, such as ENG."
      }
      if (draft.events.length === 0) return "Pick at least one event."
      const cap = runsPerHour(draft.maxRunsPerHour)
      if (cap !== null && !(Number.isInteger(cap) && cap >= 1 && cap <= 100)) {
        return "Runs per hour must be a whole number from 1 to 100."
      }
      return null
    }
  }
}

export function toTriggers(
  drafts: Array<TriggerDraft>
): Array<AutomationTriggerConfig> {
  return drafts.flatMap((draft): Array<AutomationTriggerConfig> => {
    if (draftProblem(draft)) return []
    switch (draft.kind) {
      case "schedule":
        return [{ kind: "schedule", cron: draft.cron.trim() }]
      case "github":
        return draft.repo
          ? [{ kind: "github", repo: draft.repo, events: draft.events }]
          : []
      case "slack":
        return draft.channel
          ? [
              {
                kind: "slack",
                channel: draft.channel,
                events: draft.events,
                senders: draft.senders,
                match: draft.match.trim() || null,
                max_runs_per_hour: runsPerHour(draft.maxRunsPerHour),
              },
            ]
          : []
      case "linear":
        return [
          {
            kind: "linear",
            team: draft.team.trim().toUpperCase(),
            events: draft.events,
            labels: draft.labels
              .split(",")
              .map((label) => label.trim())
              .filter(Boolean),
            project: draft.project.trim() || null,
            max_runs_per_hour: runsPerHour(draft.maxRunsPerHour),
          },
        ]
    }
  })
}

/** One part per trigger, e.g. "GitHub acme/oss: PR closed, PR merged". */
export function describeTriggers(schedule: AgentSchedule): string {
  const parts = schedule.triggers.map((trigger) => {
    if (trigger.kind === "schedule") return describeCron(trigger.cron)
    const provider = AUTOMATION_EVENT_PROVIDERS[trigger.kind]
    const events: Record<string, string> = provider.events
    const named = trigger.events.map((event) => events[event]).join(", ")
    const scope =
      trigger.kind === "github"
        ? trigger.repo
        : trigger.kind === "slack"
          ? trigger.channel
          : trigger.team
    return `${provider.label} ${scope}: ${named}`
  })
  return parts.length > 0 ? parts.join(" · ") : "No trigger"
}
