import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Link as ExternalLink } from "@langchain/macaw-components/Link"
import { Select } from "@langchain/macaw-components/Select"
import { useQuery } from "@tanstack/react-query"
import { useRef, useState } from "react"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { Link } from "@tanstack/react-router"

import {
  sharedReviewOptions,
  type PullRequestRef,
} from "@/features/reviews/lib/reviewKeys"
import {
  frontendReviewApi,
  frontendReviewKey,
  type FrontendAnnotation,
  type FrontendReviewRun,
} from "./api"
import { EvidenceViewer } from "./EvidenceViewer"
import { AskAboutChange } from "./AskAboutChange"
import { pageLink } from "./pageLinks"

export function FrontendReviewer({
  pr,
  currentHeadSha,
  runId,
  onRunChange,
}: {
  pr: PullRequestRef
  currentHeadSha?: string | null
  runId?: string
  onRunChange: (id: string) => void
}) {
  const key = frontendReviewKey(pr)
  const list = useQuery({
    queryKey: key,
    queryFn: () => frontendReviewApi.list(pr),
    ...sharedReviewOptions(pr),
  })
  const selectedId = runId ?? list.data?.runs[0]?.id
  const detail = useQuery({
    queryKey: [...frontendReviewKey(pr), selectedId],
    queryFn: () => frontendReviewApi.get(pr, selectedId!),
    enabled: Boolean(selectedId),
    ...sharedReviewOptions(pr),
  })
  const run = detail.data
  const empty = !list.isPending && !list.isError && !selectedId
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-space-5 p-space-5 max-sm:p-space-3">
        {list.isError ? (
          <ErrorCard message={list.error.message} />
        ) : detail.isError ? (
          <ErrorCard message={detail.error.message} />
        ) : (
          <>
            {list.isPending && (
              <p role="status" className="text-sm text-secondary">
                Loading Design QA reviews…
              </p>
            )}
            {empty && (
              <Card role="status" className="p-space-4 text-sm text-secondary">
                No saved comparisons. Live capture is unavailable until browser
                isolation is connected.
              </Card>
            )}
            {list.data && list.data.runs.length > 1 && (
              <label className="flex items-center gap-space-3 text-xs text-secondary">
                Review run
                <Select
                  aria-label="Review run"
                  size="md"
                  triggerClassName="w-auto min-w-0 max-w-full"
                  value={selectedId}
                  onChange={(id) => {
                    if (id) onRunChange(id)
                  }}
                  options={list.data.runs.map((item) => ({
                    value: item.id,
                    label: `${new Date(item.created_at).toLocaleString()} · ${item.status} · ${item.head_sha.slice(0, 7)}`,
                  }))}
                />
              </label>
            )}
            {selectedId && detail.isPending && (
              <p role="status" className="text-sm text-secondary">
                Loading review evidence…
              </p>
            )}
            {run && (
              <FrontendReportView
                key={run.id}
                run={run}
                currentHeadSha={currentHeadSha}
                pr={pr}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

export function FrontendReportView({
  run,
  currentHeadSha,
  pr,
}: {
  run: FrontendReviewRun
  currentHeadSha?: string | null
  pr?: PullRequestRef
}) {
  const report = run.report
  const scenarios = report?.scenarios ?? []
  const sections = useRef(new Map<string, HTMLElement>())
  const [selection, setSelection] = useState<{
    scenarioId: string
    note: FrontendAnnotation
  } | null>(null)
  const notes = scenarios.flatMap((scenario) =>
    scenario.annotations.map((note) => ({ note, scenario }))
  )
  const paired = scenarios.filter(
    (item) => item.status === "complete" && item.before_url && item.after_url
  )
  const routes = paired.filter((item) => !item.interaction).length
  const interactions = scenarios.filter((item) => item.interaction)
  const pairedInteractions = paired.filter((item) => item.interaction).length
  const production =
    report?.production_url ??
    (pr?.owner === "langchain-ai" && pr.repo === "langchainplus"
      ? "https://smith.langchain.com"
      : undefined)
  return (
    <div className="flex flex-col gap-space-5">
      {currentHeadSha && currentHeadSha !== run.head_sha && (
        <p role="status" className="text-sm text-warning-secondary">
          New PR changes have not been reviewed · saved{" "}
          {run.head_sha.slice(0, 7)}, current {currentHeadSha.slice(0, 7)}.
        </p>
      )}
      {report && (
        <Card
          aria-label="Recap"
          className="flex flex-col gap-space-3 p-space-4"
        >
          <div className="flex items-center gap-space-2">
            <h3 className="text-sm font-semibold">Recap</h3>
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
          <p className="text-sm text-secondary">
            {report.ai_review_status === "complete"
              ? report.summary
              : report.ai_review_status === "running"
                ? "Reviewing captured screenshots…"
                : report.ai_review_status === "failed"
                  ? "AI review unavailable. Saved screenshots are below."
                  : "No AI assessment yet."}
          </p>
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
                    sections.current
                      .get(scenario.id)
                      ?.scrollIntoView({ block: "start", behavior: "smooth" })
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
          {report.assessment_thread_id && (
            <Link
              to="/agents/$threadId"
              params={{ threadId: report.assessment_thread_id }}
              className="w-fit text-xs text-secondary underline underline-offset-2"
            >
              AI review conversation
            </Link>
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
                  ? run.auth_mode === "dummy"
                    ? "Authentication required"
                    : "Capture blocked"
                  : "Capture failed"}
          </h3>
          {run.error && <p className="text-sm text-secondary">{run.error}</p>}
          {run.status === "blocked" && run.auth_mode === "dummy" && (
            <p className="text-xs text-secondary">
              No preview was contacted and no page was reviewed. A disposable QA
              identity is required.
            </p>
          )}
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
              <h3 className="text-sm font-semibold">
                <span className="mr-space-2 text-secondary">{index + 1}.</span>
                {scenario.title}
              </h3>
              <div className="mt-space-1 flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-xs text-secondary">
                {scenario.metrics && (
                  <span
                    title={`Saved pixel threshold: ${scenario.metrics.threshold}. Change extent is not issue severity.`}
                  >
                    {(scenario.metrics.changed_ratio * 100).toFixed(2)}% changed
                  </span>
                )}
                {(
                  [
                    ["Production", production],
                    ["Preview", run.head_url],
                    ["Base", run.base_url],
                  ] as const
                )
                  .map(([label, origin]) => ({
                    label,
                    href: origin && pageLink(origin, scenario.path),
                  }))
                  .filter(
                    (link, linkIndex, links) =>
                      link.href &&
                      links.findIndex((item) => item.href === link.href) ===
                        linkIndex
                  )
                  .map(
                    ({ label, href }) =>
                      href && (
                        <ExternalLink
                          key={label}
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          variant="xs"
                          rightDecorator={ArrowSquareOutIcon}
                          title={href}
                        >
                          {label}
                        </ExternalLink>
                      )
                  )}
              </div>
            </div>
            <AskAboutChange
              run={run}
              scenario={scenario}
              currentHeadSha={currentHeadSha}
            />
          </div>
          {scenario.before_error ||
          scenario.after_error ||
          scenario.layout_warnings?.length ? (
            <div className="flex flex-col gap-space-1 border-t border-default px-space-4 py-space-2 text-xs text-warning-secondary">
              {scenario.before_error && <p>Base: {scenario.before_error}</p>}
              {scenario.after_error && <p>PR: {scenario.after_error}</p>}
              {scenario.layout_warnings?.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          ) : null}
          <EvidenceViewer
            scenario={scenario}
            annotation={
              selection?.scenarioId === scenario.id ? selection.note : undefined
            }
            onClearAnnotation={() => setSelection(null)}
          />
          {scenario.actions?.length || scenario.annotations.length ? (
            <details className="border-t border-default px-space-4 py-space-2 text-xs text-secondary">
              <summary className="w-fit cursor-pointer">
                {scenario.interaction ? "Interaction & notes" : "Review notes"}
              </summary>
              {scenario.actions?.length ? (
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

function ErrorCard({ message }: { message: string }) {
  return (
    <Card role="alert" className="p-space-4 text-sm text-error-secondary">
      Could not load Design QA: {message}.
    </Card>
  )
}
