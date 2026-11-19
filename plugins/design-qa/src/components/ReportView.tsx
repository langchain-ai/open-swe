import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Link } from "@langchain/macaw-components/Link"
import { cn } from "@langchain/macaw-components/utils/cn"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { useRef, useState } from "react"

import { Assessment } from "../lib/assessment"
import type {
  DesignSystemReview,
  FrontendAnnotation,
  FrontendReviewRun,
  FrontendRunStatus,
  FrontendScenario,
} from "../lib/capture"
import { pairedScenarios } from "../lib/coverage"
import { useNearViewport } from "../lib/media"
import { usePixelChange, type RunMedia } from "../lib/queries"
import { scenarioLinks } from "../lib/links"
import { repositoryOf, type PullRequestRef } from "../lib/pullRequest"
import { AskAboutChange } from "./AskAboutChange"
import { EvidenceViewer } from "./EvidenceViewer"

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
  onAsk,
}: {
  pr: PullRequestRef
  run: FrontendReviewRun
  currentHeadSha?: string | null
  /** The person asked the agent about `scenario`. */
  onAsk: (scenario: FrontendScenario) => void
}) {
  const host = usePluginHost()
  const report = run.report
  const scenarios = report?.scenarios ?? []
  const media = { repository: repositoryOf(pr), pull: pr.number, run: run.id }
  const sections = useRef(new Map<string, HTMLElement>())
  const [selection, setSelection] = useState<{
    scenarioId: string
    note: FrontendAnnotation
  } | null>(null)
  const notes = scenarios.flatMap((scenario) =>
    scenario.annotations.map((note) => ({ note, scenario }))
  )
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
  const show = (scenarioId: string) =>
    sections.current
      .get(scenarioId)
      ?.scrollIntoView({ block: "start", behavior: "smooth" })
  return (
    <div className="flex flex-col gap-space-5">
      {currentHeadSha && currentHeadSha !== run.head_sha && (
        <p role="status" className="text-sm text-warning-secondary">
          New PR changes have not been reviewed · saved{" "}
          {run.head_sha.slice(0, 7)}, current {currentHeadSha.slice(0, 7)}.
        </p>
      )}
      {reviewed &&
        report.design_system &&
        report.design_system.findings.length > 0 && (
          <DesignSystemFindings
            review={report.design_system}
            scenarios={scenarios}
            onShow={show}
          />
        )}
      {report && (
        <Card
          aria-label="Recap"
          className="flex flex-col gap-space-3 p-space-4"
        >
          <div className="flex flex-wrap items-center gap-space-2">
            <h3 className="text-sm font-semibold">Recap</h3>
            {risk && (
              <StatusBadge variant={risk.tone} size="sm">
                {risk.label}
              </StatusBadge>
            )}
            {designSystem && (
              <StatusBadge variant={designSystem.tone} size="sm">
                {designSystem.label}
              </StatusBadge>
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
          {reviewed && report.risk?.reason && (
            <p className="text-xs text-secondary">{report.risk.reason}</p>
          )}

          {reviewed ? (
            <RecapSummary summary={report.summary} />
          ) : (
            <p className="text-sm text-secondary">
              {report.ai_review_status === "running"
                ? "Reviewing captured screenshots…"
                : report.ai_review_status === "failed"
                  ? "AI review unavailable. Saved screenshots are below."
                  : "No AI assessment yet."}
            </p>
          )}
          {reviewed &&
            report.design_system &&
            report.design_system.findings.length === 0 && (
              <p className="text-xs text-tertiary">
                {Assessment.sources(report.design_system)}
              </p>
            )}
          {report.ai_review_status === "complete" && notes.length > 0 && (
            <div className="flex flex-wrap gap-space-2">
              {notes.slice(0, 3).map(({ note, scenario }, index) => (
                <Button
                  key={`${scenario.id}:${index}`}
                  size="sm"
                  color="secondary"
                  variant="plain"
                  aria-label={`Inspect ${note.title}`}
                  aria-pressed={
                    selection?.scenarioId === scenario.id &&
                    selection.note === note
                  }
                  onClick={() => {
                    setSelection({ scenarioId: scenario.id, note })
                    show(scenario.id)
                  }}
                >
                  {note.title} ↗
                </Button>
              ))}
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
      {(run.status !== "complete" || run.error) && (
        <Card role="status" className="flex flex-col gap-space-2 p-space-4">
          <h3 className="text-sm font-medium">
            {run.status === "pending"
              ? "Frontend review queued…"
              : run.status === "running"
                ? "Capturing screenshots…"
                : run.status === "blocked"
                  ? "Capture blocked"
                  : "Capture failed"}
          </h3>
          {/* A review says why: a preview or base that won't load, or a sign-in it couldn't do. */}
          {run.error && <p className="text-sm text-secondary">{run.error}</p>}
        </Card>
      )}
      {scenarios.map((scenario, index) => (
        <section
          key={scenario.id}
          aria-label={scenario.title}
          ref={(element) => {
            if (element) sections.current.set(scenario.id, element)
            else sections.current.delete(scenario.id)
          }}
          className="scroll-mt-space-4 min-w-0 overflow-hidden rounded-lg border border-default bg-surface-level-2"
        >
          <div className="flex flex-wrap items-center justify-between gap-space-3 px-space-4 py-space-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-space-2">
                <h3 className="text-sm font-semibold">
                  <span className="mr-space-2 text-secondary">
                    {index + 1}.
                  </span>
                  {scenario.title}
                </h3>
                {reviewed && report.design_system && (
                  <ScenarioFindings
                    review={report.design_system}
                    scenarioId={scenario.id}
                  />
                )}
              </div>
              <div className="mt-space-1 flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-xs text-secondary">
                <ChangedShare media={media} scenario={scenario} />
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
            <AskAboutChange
              pr={pr}
              run={run}
              scenario={scenario}
              currentHeadSha={currentHeadSha}
              onAsk={() => onAsk(scenario)}
            />
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
          <EvidenceViewer
            media={media}
            scenario={scenario}
            annotation={
              selection?.scenarioId === scenario.id ? selection.note : undefined
            }
            onClearAnnotation={() => setSelection(null)}
          />
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
                  <span className="font-medium text-primary">{note.title}</span>{" "}
                  · {note.body}
                </p>
              ))}
            </details>
          ) : null}
        </section>
      ))}
      <details className="text-xs text-secondary">
        <summary className="w-fit cursor-pointer">
          Capture details · {scenarios.length} states ·{" "}
          {report?.build_verification === "verified"
            ? "Build verified"
            : "Build unverified"}
        </summary>
        <div className="mt-space-3 flex flex-col gap-space-2">
          <p>Status: {STATUS_LABELS[run.status]}</p>
          <p>
            Coverage: {routes}/{run.paths.length} routes captured
          </p>
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
            Base {run.base_sha.slice(0, 12)} · {run.base_url}
          </p>
          <p>
            PR {run.head_sha.slice(0, 12)} · {run.head_url}
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
 * The share of a pair's pixels that differ, measured once its card comes near
 * the screen, as its screenshots load.
 */
function ChangedShare({
  media,
  scenario,
}: {
  media: RunMedia
  scenario: FrontendScenario
}) {
  const [ref, near] = useNearViewport<HTMLSpanElement>()
  const change = usePixelChange(media, scenario, near)
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

/** The recap's points as a list, and any other line as a sentence. */
function RecapSummary({ summary }: { summary: string }) {
  const blocks = Assessment.summary(summary)
  if (blocks.length === 0)
    return <p className="text-sm text-secondary">No visual changes noted.</p>
  return (
    <div className="flex flex-col gap-space-2 text-sm text-secondary">
      {blocks.map((block, index) =>
        block.kind === "text" ? (
          <p key={index}>{block.text}</p>
        ) : (
          <ul
            key={index}
            className="flex list-disc flex-col gap-space-1 pl-space-5"
          >
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex}>{item}</li>
            ))}
          </ul>
        )
      )}
    </div>
  )
}

/**
 * Where the change departs from the design system: a red banner when it
 * breaks a rule and an amber one when it only looks off, over each finding.
 */
function DesignSystemFindings({
  review,
  scenarios,
  onShow,
}: {
  review: DesignSystemReview
  scenarios: readonly FrontendScenario[]
  onShow: (scenarioId: string) => void
}) {
  const verdict = Assessment.designSystem(review)
  const ordered = [...review.findings].sort(
    (a, b) => Number(b.severity === "error") - Number(a.severity === "error")
  )
  const title =
    verdict.errors && verdict.warnings
      ? `${verdict.label} · ${verdict.warnings} warning${verdict.warnings === 1 ? "" : "s"}`
      : verdict.label
  return (
    <section aria-label="Design system" className="flex flex-col gap-space-2">
      <Banner intent={verdict.tone} title={title}>
        {Assessment.sources(review)}
      </Banner>
      <Card className="flex flex-col divide-y divide-default p-0">
        {ordered.map((finding, index) => {
          const scenario = scenarios.find(({ id }) => id === finding.scenario)
          const where = [finding.rule, finding.location].filter(Boolean)
          const error = finding.severity === "error"
          return (
            <div
              key={index}
              className={cn(
                "flex flex-col gap-space-1 border-l-2 px-space-4 py-space-3",
                error ? "border-l-error" : "border-l-warning"
              )}
            >
              <div className="flex flex-wrap items-center gap-space-2">
                <StatusBadge variant={error ? "error" : "warning"}>
                  {error ? "Violation" : "Warning"}
                </StatusBadge>
                <span className="text-sm font-medium text-primary">
                  {finding.title}
                </span>
                {scenario && (
                  <Button
                    size="sm"
                    color="secondary"
                    variant="plain"
                    onClick={() => onShow(scenario.id)}
                  >
                    {`Show ${scenario.title}`}
                  </Button>
                )}
              </div>
              {finding.body && (
                <p className="text-xs text-secondary">{finding.body}</p>
              )}
              {where.length > 0 && (
                <p className="text-xs text-tertiary">{where.join(" · ")}</p>
              )}
            </div>
          )
        })}
      </Card>
    </section>
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
