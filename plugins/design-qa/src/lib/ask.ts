import type {
  FrontendReviewRun,
  FrontendScenario,
  JsonObject,
} from "@openswe/plugin-sdk"

import {
  pullRequestLabel,
  pullRequestUrl,
  repositoryOf,
  type PullRequestRef,
} from "./pullRequest"

/**
 * The dock's chat about one pull request. Chat names take only letters,
 * digits and `._:@/-`, so the number follows `/pull/` rather than `#`.
 */
export const chatName = (pr: PullRequestRef) =>
  `${repositoryOf(pr)}/pull/${pr.number}`

/** The dock's limits: shared context, draft text and each attached image. */
const MAX_CONTEXT = 8_000
const MAX_DRAFT = 20_000
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024

export const clip = (text: string, max: number) =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text

const APP =
  "Design QA: saved before and after screenshots of a pull request's frontend"

/** What the agent learns with each message sent from the dock while a pull request is open. */
export function pullRequestContext(
  pr: PullRequestRef,
  run: FrontendReviewRun,
  current: { title?: string | null; headSha?: string | null } = {}
): JsonObject {
  return {
    app: APP,
    pullRequest: pullRequestLabel(pr),
    url: pullRequestUrl(pr),
    title: current.title ? clip(current.title, 300) : null,
    run: run.id,
    runStatus: run.status,
    baseSha: run.base_sha,
    headSha: run.head_sha,
    currentHeadSha: current.headSha ?? null,
    buildVerification: run.report?.build_verification ?? "unverified",
  }
}

/** The pull request's context narrowed to one scenario, within the dock's size limit. */
export function scenarioContext(
  pr: PullRequestRef,
  run: FrontendReviewRun,
  scenario: FrontendScenario,
  currentHeadSha: string | null | undefined
): JsonObject {
  const blocker = (text: string | null) => (text ? clip(text, 400) : null)
  const build = (items: number): JsonObject => ({
    ...pullRequestContext(pr, run, { headSha: currentHeadSha }),
    scenario: {
      id: scenario.id,
      title: clip(scenario.title, 200),
      path: clip(scenario.path, 500),
      interaction: scenario.interaction,
      status: scenario.status,
      baseScreenshot: scenario.before_url !== null,
      prScreenshot: scenario.after_url !== null,
      replaySteps: scenario.actions
        .slice(0, items)
        .map((step) => clip(step, 200)),
      blockers: {
        base: blocker(scenario.before_error),
        pr: blocker(scenario.after_error),
        capture: blocker(scenario.error),
      },
      layoutWarnings: scenario.layout_warnings
        .slice(0, items)
        .map((warning) => clip(warning, 200)),
    },
  })
  for (const items of [12, 3]) {
    const context = build(items)
    if (JSON.stringify(context).length <= MAX_CONTEXT) return context
  }
  return build(0)
}

export interface Attachment {
  side: "Base" | "PR"
  name: string
  blob: Blob
}

/** The question the dock's composer starts with; the person edits it before sending. */
export function askDraft({
  pr,
  run,
  scenario,
  currentHeadSha,
  attached,
  tooLarge,
}: {
  pr: PullRequestRef
  run: FrontendReviewRun
  scenario: FrontendScenario
  currentHeadSha: string | null | undefined
  attached: readonly Attachment[]
  tooLarge: readonly Attachment[]
}): string {
  const steps = scenario.actions
  const megabytes = (blob: Blob) => (blob.size / 1_000_000).toFixed(1)
  const details = [
    `Design QA: ${scenario.title}`,
    `Route: ${scenario.path} · ${scenario.width ?? "?"} × ${scenario.height ?? "?"}`,
    `Checkpoint: ${scenario.id} · ${scenario.interaction ? "Interaction state" : "Page capture"} · ${scenario.status}`,
    `Pull request: ${pullRequestLabel(pr)} · ${pullRequestUrl(pr)}`,
    `Run: ${run.id}`,
    `Base commit: ${run.base_sha}`,
    `PR commit: ${run.head_sha}`,
    currentHeadSha && currentHeadSha !== run.head_sha
      ? `Stale capture: the current PR commit is ${currentHeadSha}.`
      : null,
    `Build verification: ${run.report?.build_verification ?? "unverified"}`,
    `Attached screenshots: ${
      attached.length
        ? attached.map(({ side, name }) => `${side} (${name})`).join("; ")
        : "none"
    }`,
    scenario.before_url ? null : "Base screenshot: not captured.",
    scenario.after_url ? null : "PR screenshot: not captured.",
    ...tooLarge.map(
      ({ side, blob }) =>
        `${side} screenshot: not attached, ${megabytes(blob)} MB is over the 10 MB limit.`
    ),
    steps.length
      ? `Planned replay steps:\n${steps.map((step, index) => `${index + 1}. ${clip(step, 500)}`).join("\n")}`
      : "Interaction replay: not captured.",
    scenario.before_error
      ? `Base capture blocker: ${clip(scenario.before_error, 1_000)}`
      : null,
    scenario.after_error
      ? `PR capture blocker: ${clip(scenario.after_error, 1_000)}`
      : null,
    scenario.error ? `Capture blocker: ${clip(scenario.error, 1_000)}` : null,
    ...scenario.layout_warnings.map(
      (warning) => `Potential layout issue: ${clip(warning, 500)}`
    ),
    scenario.metrics
      ? `Recorded pixel change: ${(scenario.metrics.changed_ratio * 100).toFixed(2)}% (${scenario.metrics.changed_pixels} pixels; threshold ${scenario.metrics.threshold}).`
      : null,
    ...scenario.annotations.map(
      (note) => `Review note: ${note.title}: ${clip(note.body, 1_000)}`
    ),
    "These are the original saved screenshots; viewer overlays, local masks, and crops are not included. Planned steps alone do not establish successful coverage.",
  ].filter((line): line is string => line !== null)
  return clip(
    `Can you review this Design QA change and its interaction state?\n\n${details.join("\n\n")}`,
    MAX_DRAFT
  )
}
