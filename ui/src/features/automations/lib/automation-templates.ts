import { PackageRegularIcon } from "@langchain/macaw-components/icons"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { BinocularsIcon } from "@phosphor-icons/react/dist/ssr/Binoculars"
import { BugIcon } from "@phosphor-icons/react/dist/ssr/Bug"
import { CalendarBlankIcon } from "@phosphor-icons/react/dist/ssr/CalendarBlank"
import { ChatsCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatsCircle"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LightbulbIcon } from "@phosphor-icons/react/dist/ssr/Lightbulb"
import { ListChecksIcon } from "@phosphor-icons/react/dist/ssr/ListChecks"
import { NotePencilIcon } from "@phosphor-icons/react/dist/ssr/NotePencil"
import { SunHorizonIcon } from "@phosphor-icons/react/dist/ssr/SunHorizon"

export interface AutomationTemplate {
  /** Stable id used as the `?template=` search param on the new-automation route. */
  id: string
  name: string
  description: string
  /** Seed instructions sent to Robin on each scheduled run. */
  prompt: string
  /** Default 5-field cron expression (UTC). */
  schedule: string
  icon: IconComponent
}

export const AUTOMATION_TEMPLATES: ReadonlyArray<AutomationTemplate> = [
  {
    id: "daily-briefing",
    name: "Daily briefing",
    description:
      "What needs your attention today across Slack, GitHub, Linear, and your calendar.",
    schedule: "0 8 * * 1-5",
    icon: SunHorizonIcon,
    prompt: `Put together my briefing for today. Using the tools I have connected, look at what needs my attention: Slack threads that mention me or are waiting on my reply, pull requests and issues assigned to me or waiting on my review, Linear issues due soon, and today's meetings if my calendar is connected. Lead with the three things I should do first, then list the rest grouped by source with links. Keep it short enough to read in two minutes.`,
  },
  {
    id: "slack-catch-up",
    name: "Slack catch-up",
    description:
      "Summarize the channels you follow and flag anything waiting on you.",
    schedule: "0 8 * * 1-5",
    icon: ChatsCircleIcon,
    prompt: `Catch me up on Slack since yesterday. Read the busiest public channels I'm in and summarize each in a few bullets: decisions made, questions still open, and anything that mentions me or my team. Flag threads where someone is waiting on me, with a link and a one-line suggested reply. Skip channels where nothing important happened.`,
  },
  {
    id: "meeting-prep",
    name: "Meeting prep",
    description:
      "A short brief for each meeting on your calendar: attendees, context, and agenda.",
    schedule: "0 8 * * 1-5",
    icon: CalendarBlankIcon,
    prompt: `Prepare me for today's meetings. For each meeting on my connected calendar, write a short brief: who is attending and what they work on, the recent context (related Slack threads, docs, issues, or pull requests), open questions to raise, and a suggested agenda if there isn't one. If no calendar is connected, say so and stop.`,
  },
  {
    id: "weekly-review",
    name: "Weekly review",
    description:
      "A Friday summary of what you and your team got done this week.",
    schedule: "0 16 * * 5",
    icon: ListChecksIcon,
    prompt: `Write my weekly review. Gather what happened this week across the tools I have connected: work I shipped or closed, decisions made in Slack, notable pull requests and issues, and anything that slipped. Organize it into Done, In progress, and Blocked, with links. End with three suggested priorities for next week.`,
  },
  {
    id: "content-ideas",
    name: "Content ideas",
    description:
      "Draft a few post ideas each week from the latest news in your industry.",
    schedule: "0 9 * * 1",
    icon: LightbulbIcon,
    prompt: `Search the web for the most interesting news and discussions from the past week in my industry, and anything new we shipped. Suggest five post ideas, each with a working title, a two-sentence angle, and links to the sources it draws on. Favor ideas with a clear point of view over news recaps.`,
  },
  {
    id: "monitor-topic",
    name: "Monitor a topic",
    description:
      "Watch for news or mentions of a topic, competitor, or keyword.",
    schedule: "0 9 * * *",
    icon: BinocularsIcon,
    prompt: `Search the web for news and discussion from the past day about [replace with the topic, competitor, or keyword to watch]. Summarize anything new and notable in a few bullets with links, and say why it matters. If nothing meaningful happened, reply with one line saying so.`,
  },
  {
    id: "pr-review-digest",
    name: "PR review digest",
    description:
      "Summarize open pull requests, review status, and what needs attention.",
    schedule: "0 9 * * 1-5",
    icon: GitPullRequestIcon,
    prompt: `List the open pull requests on this repository and produce a concise review digest. For each PR include the title, author, age, whether CI is passing, review status, and any merge conflicts. Group them into "Needs review", "Changes requested", and "Ready to merge". Call out anything that has been waiting more than two days. Post the digest as a comment on the most relevant tracking issue, or summarize it in your final reply if there is none.`,
  },
  {
    id: "issue-triage",
    name: "Issue triage",
    description:
      "Review newly opened issues, label them, and flag likely duplicates.",
    schedule: "30 8 * * 1-5",
    icon: BugIcon,
    prompt: `Review issues opened on this repository since the last run. For each one, summarize the report, suggest appropriate labels (bug, feature, question, etc.), and identify likely duplicates by searching existing issues. Leave a short triage comment on each new issue with your assessment and, where confident, apply labels. End with a summary of what you triaged.`,
  },
  {
    id: "release-notes",
    name: "Release notes drafter",
    description:
      "Draft user-facing release notes from pull requests merged recently.",
    schedule: "0 16 * * 5",
    icon: NotePencilIcon,
    prompt: `Gather the pull requests merged into the default branch since the last release (or in the past week). Draft concise, user-facing release notes grouped into Features, Fixes, and Maintenance, written for end users rather than contributors. Include PR numbers for traceability and post the draft in your final reply.`,
  },
  {
    id: "dependency-updates",
    name: "Dependency update check",
    description:
      "Scan for outdated packages, security patches, and breaking changes.",
    schedule: "0 9 * * 1",
    icon: PackageRegularIcon,
    prompt: `Scan this repository's dependency manifests and lockfiles for outdated packages and known security advisories. Prioritize security patches and safe minor/patch upgrades, and note any major upgrades that may contain breaking changes. Open a draft pull request that bumps the low-risk, well-tested upgrades, and summarize the riskier ones for manual review.`,
  },
]

export function automationTemplateById(
  id: string | undefined | null
): AutomationTemplate | undefined {
  if (!id) return undefined
  return AUTOMATION_TEMPLATES.find((template) => template.id === id)
}
