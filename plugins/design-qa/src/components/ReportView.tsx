import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Link } from "@langchain/macaw-components/Link"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { Text } from "@langchain/macaw-components/Text"
import { cn } from "@langchain/macaw-components/utils/cn"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { useHostContext, usePluginHost } from "@openswe/plugin-sdk/react"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { CaretUpIcon } from "@phosphor-icons/react/dist/ssr/CaretUp"
import { MoonIcon } from "@phosphor-icons/react/dist/ssr/Moon"
import { SunIcon } from "@phosphor-icons/react/dist/ssr/Sun"
import { useState } from "react"

import { MarkdownDescription } from "./MarkdownDescription"
import { Assessment } from "../lib/assessment"
import {
  CaptureAttempt,
  StoredCapture,
  type DesignSystemReview,
  type FrontendReviewRun,
  type FrontendRunStatus,
  type FrontendScenario,
  type MissedSurface,
  type ScenarioShots,
  type ShotTheme,
} from "../lib/capture"
import { pairedScenarios } from "../lib/coverage"
import { useNearViewport } from "../lib/media"
import { usePixelChange, type RunMedia } from "../lib/queries"
import { scenarioLinks } from "../lib/links"
import { repositoryOf, type PullRequestRef } from "../lib/pullRequest"
import { AskAboutChange } from "./AskAboutChange"
import { EvidenceViewer } from "./EvidenceViewer"
import { useUsage } from "../lib/usage"

const STATUS_LABELS: Record<FrontendRunStatus, string> = {
  pending: "Queued",
  running: "Capturing",
  complete: "Complete",
  blocked: "Blocked",
  failed: "Failed",
}

/** One run's recap, scenario cards and capture details. */
export function ReportView({
  pr,
  run,
  currentHeadSha,
  description,
  onAsk,
}: {
  pr: PullRequestRef
  run: FrontendReviewRun
  currentHeadSha?: string | null
  description?: string | null
  /** The person asked the agent about `scenario`, in the theme of `shots`. */
  onAsk: (scenario: FrontendScenario, shots: ScenarioShots) => void
}) {
  const host = usePluginHost()
  const report = run.report
  const scenarios = report?.scenarios ?? []
  const media = { repository: repositoryOf(pr), pull: pr.number, run: run.id }
  const [expanded, setExpanded] = useState(false)
  const paired = pairedScenarios(scenarios)
  const routes = paired.filter((item) => !item.interaction).length
  const interactions = scenarios.filter((item) => item.interaction)
  const pairedInteractions = paired.filter((item) => item.interaction).length
  const assessmentThread = report?.assessment_thread_id
  const reviewed = report?.ai_review_status === "complete"
  const risk = reviewed && report.risk ? Assessment.risk(report.risk) : null
  const designSystem =
    reviewed && report.design_system
      ? Assessment.designSystem(report.design_system)
      : null
  return (
    <div className="flex flex-col gap-space-5">
      {currentHeadSha && currentHeadSha !== run.head_sha && (
        <p role="status" className="text-sm text-warning-secondary">
          New PR changes have not been reviewed. Saved{" "}
          {run.head_sha.slice(0, 7)}, current {currentHeadSha.slice(0, 7)}.
        </p>
      )}
      {report && (
        <Card
          aria-label="PR Description"
          className="flex flex-col gap-space-3 p-space-4"
        >
          <div className="flex flex-wrap items-center gap-space-2">
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              aria-expanded={expanded}
              rightDecorator={expanded ? CaretUpIcon : CaretDownIcon}
              onClick={() => setExpanded(!expanded)}
            >
              PR Description
            </Button>
            {risk && (
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                aria-label={`Risk ${report.risk?.score}/5: show assessment`}
                aria-expanded={expanded}
                onClick={() => setExpanded(true)}
              >
                <StatusBadge variant={risk.tone} size="sm">
                  {`Risk ${report.risk?.score}/5`}
                </StatusBadge>
              </Button>
            )}
            {designSystem && (
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                aria-label={`${designSystem.label}: show findings`}
                aria-expanded={expanded}
                onClick={() => setExpanded(true)}
              >
                <StatusBadge variant={designSystem.tone} size="sm">
                  {designSystem.label}
                </StatusBadge>
              </Button>
            )}
            {report.ai_review_status !== "complete" && (
              <Badge color="secondary" size="xs">
                {report.ai_review_status === "running"
                  ? "Reviewing"
                  : report.ai_review_status === "failed"
                    ? "Unavailable"
                    : "Not run"}
              </Badge>
            )}
          </div>
          {expanded && (
            <div className="flex flex-col gap-space-3">
              <MarkdownDescription body={description} />
              {report.risk?.reason && (
                <Text as="p" variant="sm" color="secondary">
                  {report.risk.reason}
                </Text>
              )}
              {reviewed &&
                report.design_system &&
                report.design_system.findings.length > 0 && (
                  <DesignSystemFindings
                    review={report.design_system}
                    scenarios={scenarios}
                  />
                )}
            </div>
          )}
          {report.assessment_error && (
            <p role="status" className="text-xs text-warning-secondary">
              {report.assessment_error}
            </p>
          )}
          {assessmentThread && (
            <button
              type="button"
              className="w-fit text-xs text-secondary underline underline-offset-2"
              onClick={() => void host.nav.openThread(assessmentThread)}
            >
              AI review conversation
            </button>
          )}
        </Card>
      )}
      {report && report.missed.length > 0 && (
        <MissedSurfaces missed={report.missed} />
      )}
      {(run.status !== "complete" || run.error) && (
        <Card role="status" className="flex flex-col gap-space-2 p-space-4">
          <h3 className="text-sm font-medium">
            {run.status === "pending"
              ? "Frontend review queued…"
              : run.status === "running"
                ? "Capturing screenshots…"
                : run.status === "blocked"
                  ? CaptureAttempt.isPreviewPending(run.error)
                    ? "Preview pending"
                    : "Capture blocked"
                  : "Capture failed"}
          </h3>
          {run.error && <p className="text-sm text-secondary">{run.error}</p>}
        </Card>
      )}
      {scenarios.map((scenario, index) => (
        <ScenarioCard
          key={scenario.id}
          pr={pr}
          run={run}
          scenario={scenario}
          index={index}
          media={media}
          designSystem={reviewed ? report.design_system : null}
          currentHeadSha={currentHeadSha}
          onAsk={onAsk}
        />
      ))}
      <details className="text-xs text-secondary">
        <summary className="w-fit cursor-pointer">
          Capture details ({scenarios.length} states)
        </summary>
        <div className="mt-space-3 flex flex-col gap-space-2">
          <p>Status: {STATUS_LABELS[run.status]}</p>
          <p>
            Build:{" "}
            {report?.build_verification === "verified"
              ? "Verified"
              : "Unverified"}
          </p>
          <p>Changed routes captured: {routes}</p>
          <p>
            Interactions: {pairedInteractions}/{interactions.length} states
            paired
          </p>
          <p>
            AI review:{" "}
            {report?.ai_review_status === "complete"
              ? "Complete"
              : report?.ai_review_status === "running"
                ? "Reviewing"
                : report?.ai_review_status === "failed"
                  ? "Failed"
                  : "Not run"}
          </p>
          <p>
            Base {run.base_sha.slice(0, 12)}: {run.base_url}
          </p>
          <p>
            PR {run.head_sha.slice(0, 12)}: {run.head_url}
          </p>
          {report?.build_verification !== "verified" && (
            <p>Preview builds have not been verified against these commits.</p>
          )}
          <p>
            Recordings:{" "}
            {report?.video_status === "recorded" ? "Available" : "Not captured"}
          </p>
        </div>
      </details>
    </div>
  )
}

/**
 * One scenario: its links, pixel change and screenshots, in light or, when it
 * was also shot in dark mode, dark. The theme starts on the dashboard's and
 * switching it changes only this card.
 */
function ScenarioCard({
  pr,
  run,
  scenario,
  index,
  media,
  designSystem,
  currentHeadSha,
  onAsk,
}: {
  pr: PullRequestRef
  run: FrontendReviewRun
  scenario: FrontendScenario
  index: number
  media: RunMedia
  /** The run's design system check, once reviewed. */
  designSystem: DesignSystemReview | null
  currentHeadSha: string | null | undefined
  onAsk: (scenario: FrontendScenario, shots: ScenarioShots) => void
}) {
  const host = usePluginHost()
  const { theme: dashboard } = useHostContext()
  const [chosen, setChosen] = useState<ShotTheme | null>(null)
  const record = useUsage()
  const dark = StoredCapture.hasDark(scenario)
  const shots = StoredCapture.shots(scenario, chosen ?? dashboard)
  return (
    <section
      aria-label={scenario.title}
      tabIndex={-1}
      className="scroll-mt-space-4 min-w-0 overflow-hidden rounded-lg border border-default bg-surface-level-2 outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      <div className="flex flex-wrap items-center justify-between gap-space-3 px-space-4 py-space-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-space-2">
            <h3 className="text-sm font-semibold">
              <span className="mr-space-2 text-secondary">{index + 1}.</span>
              {scenario.title}
            </h3>
            {designSystem && (
              <ScenarioFindings
                review={designSystem}
                scenarioId={scenario.id}
              />
            )}
          </div>
          <div className="mt-space-1 flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-xs text-secondary">
            <ChangedShare media={media} shots={shots} />
            {scenarioLinks(run, scenario.path).map(({ label, href }) => (
              <Link
                key={label}
                href={href}
                variant="xs"
                rightDecorator={ArrowSquareOutIcon}
                title={href}
                onClick={(event) => {
                  event.preventDefault()
                  void host.nav.openExternal(href)
                }}
              >
                {label}
              </Link>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-space-2">
          {dark && (
            <GroupedTabs
              size="sm"
              value={shots.theme}
              onChange={(next) => {
                record(next === "dark" ? "theme_dark" : "theme_light")
                setChosen(next)
              }}
              options={[
                { value: "light", display: "Light", icon: SunIcon },
                { value: "dark", display: "Dark", icon: MoonIcon },
              ]}
            />
          )}
          <AskAboutChange
            pr={pr}
            run={run}
            scenario={scenario}
            shots={shots}
            currentHeadSha={currentHeadSha}
            onAsk={() => onAsk(scenario, shots)}
          />
        </div>
      </div>
      {scenario.before_error ||
      scenario.after_error ||
      scenario.layout_warnings.length ? (
        <div className="flex flex-col gap-space-1 border-t border-default px-space-4 py-space-2 text-xs text-warning-secondary">
          {scenario.before_error && <p>Base: {scenario.before_error}</p>}
          {scenario.after_error && <p>PR: {scenario.after_error}</p>}
          {scenario.layout_warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </div>
      ) : null}
      <EvidenceViewer media={media} scenario={scenario} shots={shots} />
      {scenario.actions.length || scenario.annotations.length ? (
        <details className="border-t border-default px-space-4 py-space-2 text-xs text-secondary">
          <summary className="w-fit cursor-pointer">
            {scenario.interaction ? "Interaction & notes" : "Review notes"}
          </summary>
          {scenario.actions.length ? (
            <ol className="mt-space-2 list-inside list-decimal">
              {scenario.actions.map((action, actionIndex) => (
                <li key={actionIndex}>{action}</li>
              ))}
            </ol>
          ) : null}
          {scenario.annotations.map((note, noteIndex) => (
            <p key={noteIndex} className="mt-space-2">
              <span className="font-medium text-primary">{note.title}:</span>{" "}
              {note.body}
            </p>
          ))}
        </details>
      ) : null}
    </section>
  )
}

/**
 * The share of a pair's pixels that differ, measured once its card comes near
 * the screen, as its screenshots load.
 */
function ChangedShare({
  media,
  shots,
}: {
  media: RunMedia
  shots: ScenarioShots
}) {
  const [ref, near] = useNearViewport<HTMLSpanElement>()
  const change = usePixelChange(media, shots, near)
  return (
    <span ref={ref}>
      {change.data ? (
        <span title="Pixels that differ between the two screenshots. Change extent is not issue severity.">
          {(change.data.ratio * 100).toFixed(2)}% changed
        </span>
      ) : change.data === null ? (
        <span title="The screenshots differ in size, or are too large to compare here.">
          Not compared
        </span>
      ) : null}
    </span>
  )
}

/**
 * Where the change departs from the design system, worst first, one line each:
 * what's wrong, where in the code, and the scenario that shows it. The rule
 * and the reviewer's explanation show on hover or focus.
 */
function DesignSystemFindings({
  review,
  scenarios,
}: {
  review: DesignSystemReview
  scenarios: readonly FrontendScenario[]
}) {
  const ordered = [...review.findings].sort(
    (a, b) => Number(b.severity === "error") - Number(a.severity === "error")
  )
  return (
    <Card
      aria-label="Design system findings"
      className="flex flex-col divide-y divide-default p-0"
    >
      {ordered.map((finding, index) => {
        const scenario = scenarios.find(({ id }) => id === finding.scenario)
        const error = finding.severity === "error"
        const explained = Boolean(finding.body || finding.rule)
        return (
          <div
            key={index}
            className={cn(
              "flex min-w-0 flex-wrap items-center gap-x-space-3 gap-y-space-1 border-l-2 px-space-4 py-space-2",
              error ? "border-l-error" : "border-l-warning"
            )}
          >
            <StatusBadge variant={error ? "error" : "warning"}>
              {error ? "Violation" : "Warning"}
            </StatusBadge>
            <Tooltip
              title={finding.body || undefined}
              description={finding.rule || undefined}
            >
              <span
                tabIndex={explained ? 0 : undefined}
                className="min-w-0 rounded-sm text-sm font-medium text-primary outline-none focus-visible:ring-2 focus-visible:ring-focus"
              >
                {finding.title}
              </span>
            </Tooltip>
            {finding.location && (
              <span
                className="min-w-0 truncate font-mono text-xs text-tertiary"
                title={finding.location}
              >
                {finding.location}
              </span>
            )}
            {scenario && (
              <Text as="span" variant="xs" color="secondary">
                {scenario.title}
              </Text>
            )}
            {finding.body && (
              <Text as="p" variant="sm" color="secondary" className="w-full">
                {finding.body}
              </Text>
            )}
            {finding.rule && (
              <Text as="p" variant="xs" color="secondary" className="w-full">
                {finding.rule}
              </Text>
            )}
          </div>
        )
      })}
    </Card>
  )
}

/** The surfaces the review mapped from the diff but couldn't capture, with why. */
function MissedSurfaces({ missed }: { missed: readonly MissedSurface[] }) {
  return (
    <Card
      aria-label="Not captured"
      className="flex flex-col divide-y divide-default p-0"
    >
      <h3 className="px-space-4 py-space-2 text-sm font-semibold">
        Not captured ({missed.length})
      </h3>
      {missed.map((surface, index) => (
        <div
          key={index}
          className="flex min-w-0 flex-col gap-space-1 border-l-2 border-l-warning px-space-4 py-space-2"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-x-space-3">
            <span className="text-sm font-medium text-primary">
              {surface.title}
            </span>
            {surface.path && (
              <span
                className="min-w-0 truncate font-mono text-xs text-tertiary"
                title={surface.path}
              >
                {surface.path}
              </span>
            )}
          </div>
          {surface.reason && (
            <p className="text-xs text-secondary">{surface.reason}</p>
          )}
        </div>
      ))}
    </Card>
  )
}

/** A scenario's design system findings, as one colored count beside its title. */
function ScenarioFindings({
  review,
  scenarioId,
}: {
  review: DesignSystemReview
  scenarioId: string
}) {
  const findings = review.findings.filter(
    ({ scenario }) => scenario === scenarioId
  )
  if (findings.length === 0) return null
  const verdict = Assessment.designSystem({ ...review, findings })
  return <StatusBadge variant={verdict.tone}>{verdict.label}</StatusBadge>
}
