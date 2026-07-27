import {
  BookOpenIcon,
  BugIcon,
  FlaskIcon,
  GitPullRequestIcon,
  NotePencilIcon,
  PackageIcon,
  ShieldCheckIcon,
} from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"

export interface AutomationTemplate {
  /** Stable id used as the `?template=` search param on the new-automation route. */
  id: string
  name: string
  description: string
  /** Seed instructions sent to Jarvis on each scheduled run. */
  prompt: string
  /** Default 5-field cron expression (UTC). */
  schedule: string
  icon: Icon
}

export const AUTOMATION_TEMPLATES: ReadonlyArray<AutomationTemplate> = [
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
    id: "dependency-updates",
    name: "Dependency update check",
    description:
      "Scan for outdated packages, security patches, and breaking changes.",
    schedule: "0 9 * * 1",
    icon: PackageIcon,
    prompt: `Scan this repository's dependency manifests and lockfiles for outdated packages and known security advisories. Prioritize security patches and safe minor/patch upgrades, and note any major upgrades that may contain breaking changes. Open a draft pull request that bumps the low-risk, well-tested upgrades, and summarize the riskier ones for manual review.`,
  },
  {
    id: "flaky-test-tracker",
    name: "Flaky test tracker",
    description:
      "Find tests that pass and fail intermittently across recent CI runs.",
    schedule: "0 9 * * 1",
    icon: FlaskIcon,
    prompt: `Inspect recent CI runs for this repository and identify tests that have both passed and failed on the same or similar commits — likely flaky tests. For each suspect, note the test name, how often it failed, and any common error output. Open an issue (or update an existing tracking issue) listing the flaky tests ranked by failure frequency, with links to the relevant runs.`,
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
    id: "docs-freshness",
    name: "Docs freshness check",
    description:
      "Flag documentation that has drifted out of sync with recent code changes.",
    schedule: "0 9 * * 3",
    icon: BookOpenIcon,
    prompt: `Compare recent code changes against this repository's documentation (README, docs/, and inline guides). Identify documentation that is stale, references removed APIs, or omits newly added behavior. Open a draft pull request with focused fixes for the clear-cut cases, and summarize anything ambiguous that needs a human decision.`,
  },
  {
    id: "security-audit",
    name: "Security audit",
    description: "Scan the codebase for hardcoded secrets and risky patterns.",
    schedule: "0 7 * * 1",
    icon: ShieldCheckIcon,
    prompt: `Audit this repository for security issues: hardcoded secrets or credentials, unsafe handling of user input, overly permissive configuration, and dependencies with known vulnerabilities. Do not include any secret values in your output. Open an issue summarizing the findings ranked by severity, with file references and a suggested remediation for each.`,
  },
]

export function automationTemplateById(
  id: string | undefined | null
): AutomationTemplate | undefined {
  if (!id) return undefined
  return AUTOMATION_TEMPLATES.find((template) => template.id === id)
}
