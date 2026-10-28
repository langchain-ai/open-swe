import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import {
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "@tanstack/react-router"

import {
  sharedReviewOptions,
  type PullRequestRef,
} from "@/features/reviews/lib/reviewKeys"
import {
  frontendReviewApi,
  frontendReviewKey,
  type FrontendReviewRun,
  type FrontendReviewInput,
} from "./api"
import { EvidenceViewer } from "./EvidenceViewer"

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
  const client = useQueryClient()
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
  const [setupOpen, setSetupOpen] = useState(false)
  const mutationKey = [...key, "start"]
  const starting = useIsMutating({ mutationKey, exact: true }) > 0
  const captureInProgress =
    list.data?.runs.some(
      (run) => run.status === "pending" || run.status === "running"
    ) ?? false
  const start = useMutation({
    mutationKey,
    mutationFn: (input: FrontendReviewInput) =>
      frontendReviewApi.start(pr, input),
    onSuccess: async (run) => {
      client.setQueryData([...key, run.id], run)
      client.setQueryData<{ runs: FrontendReviewRun[] }>(key, (current) => ({
        runs: [
          run,
          ...(current?.runs ?? []).filter((item) => item.id !== run.id),
        ],
      }))
      await client.invalidateQueries({ queryKey: key })
    },
    meta: { errorTitle: "Could not start Design QA review" },
  })

  const run = detail.data
  const empty = !list.isPending && !list.isError && !selectedId
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-space-5 p-space-5 max-sm:p-space-3">
        <div className="flex flex-wrap items-start justify-between gap-space-3">
          <div>
            <div className="mb-space-2 flex items-center gap-space-2">
              <h2 className="text-lg font-semibold">Design QA</h2>
              <Badge color="secondary" size="xs">
                Preview
              </Badge>
            </div>
            <p className="max-w-2xl text-sm text-secondary">
              Compare the actual PR preview with its base. Inspect pixel changes
              and the screenshot evidence behind each finding.
            </p>
          </div>
          <div className="flex items-center gap-space-2">
            <Button
              color="secondary"
              variant="outlined"
              size="sm"
              onClick={() => {
                void list.refetch()
                if (selectedId) void detail.refetch()
              }}
            >
              Refresh
            </Button>
            <Button
              size="sm"
              disabled={list.isPending || starting || captureInProgress}
              onClick={() => setSetupOpen(!setupOpen)}
            >
              {setupOpen ? "Close setup" : "New Design QA review"}
            </Button>
          </div>
        </div>
        {(setupOpen || empty) && (
          <ReviewSetup
            pr={pr}
            pending={list.isPending || starting || captureInProgress}
            error={start.error?.message}
            onStart={(input) =>
              start.mutate(input, {
                onSuccess: (created) => {
                  onRunChange(created.id)
                  setSetupOpen(false)
                },
              })
            }
          />
        )}
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
              <Card className="flex flex-col items-start gap-space-3 p-space-6">
                <h3 className="text-base font-medium">
                  Visual evidence belongs with the code review.
                </h3>
                <p className="max-w-xl text-sm text-secondary">
                  Start with matching base and PR preview URLs. Each run keeps
                  its own screenshots, changed pixels, recordings, and coverage.
                  No Design QA review has run for this PR yet.
                </p>
              </Card>
            )}
            {list.data && list.data.runs.length > 0 && (
              <label className="flex items-center gap-space-3 text-xs text-secondary">
                Review run
                <select
                  aria-label="Review run"
                  className="max-w-full rounded-sm border border-default bg-surface-level-1 px-space-3 py-space-2 text-sm text-primary"
                  value={selectedId ?? ""}
                  onChange={(event) => onRunChange(event.target.value)}
                >
                  {list.data.runs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {new Date(item.created_at).toLocaleString()} ·{" "}
                      {item.status} · {item.head_sha.slice(0, 7)}
                    </option>
                  ))}
                </select>
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
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

function ReviewSetup({
  pr,
  pending,
  error,
  onStart,
}: {
  pr: PullRequestRef
  pending: boolean
  error?: string
  onStart: (input: {
    base_url: string
    head_url: string
    paths: string[]
    auth_mode: "dummy" | "none"
  }) => void
}) {
  const [base, setBase] = useState("")
  const [head, setHead] = useState(
    pr.owner === "langchain-ai" && pr.repo === "langchainplus"
      ? `https://pr-${pr.number}-fe.review.smith.langchain.com`
      : ""
  )
  const [paths, setPaths] = useState("/")
  const [auth, setAuth] = useState<"dummy" | "none">("dummy")
  return (
    <Card className="p-space-4">
      <form
        className="flex flex-col gap-space-4"
        onSubmit={(event) => {
          event.preventDefault()
          onStart({
            base_url: base.trim(),
            head_url: head.trim(),
            paths: paths
              .split(",")
              .map((path) => path.trim())
              .filter(Boolean),
            auth_mode: auth,
          })
        }}
      >
        <div>
          <h3 className="text-sm font-medium">Capture a Design QA review</h3>
          <p className="mt-space-1 text-xs text-secondary">
            Use the same routes, viewport, and data on both versions. Use a base
            preview built from the PR base commit.
          </p>
          <p
            role="status"
            className="mt-space-2 text-xs text-warning-secondary"
          >
            Live capture is currently blocked until the browser provider
            enforces network isolation. Dummy login is available to verify the
            review flow.
          </p>
        </div>
        <div className="grid gap-space-3 md:grid-cols-2">
          <label className="flex flex-col gap-space-2 text-xs font-medium">
            Base preview URL
            <Input
              aria-label="Base preview URL"
              type="url"
              required
              value={base}
              onChange={setBase}
              placeholder="https://base-preview.example.com"
              disabled={pending}
            />
          </label>
          <label className="flex flex-col gap-space-2 text-xs font-medium">
            PR preview URL
            <Input
              aria-label="PR preview URL"
              type="url"
              required
              value={head}
              onChange={setHead}
              placeholder="https://pr-preview.example.com"
              disabled={pending}
            />
          </label>
        </div>
        <label className="flex flex-col gap-space-2 text-xs font-medium">
          Routes to inspect
          <Input
            aria-label="Routes to inspect"
            required
            value={paths}
            onChange={setPaths}
            placeholder="/, /projects"
            disabled={pending}
          />
          <span className="font-normal text-secondary">
            Up to 5 routes, separated by commas.
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-space-3">
          <GroupedTabs
            size="sm"
            value={auth}
            onChange={setAuth}
            options={[
              { value: "dummy", display: "Dummy login" },
              { value: "none", display: "Public preview" },
            ]}
          />
          <p className="text-xs text-secondary">
            {auth === "dummy"
              ? "Records an authentication blocker without attempting a login or contacting the preview."
              : "Records the browser isolation blocker without contacting the preview. Public page capture requires enforced browser network isolation."}
          </p>
        </div>
        {error && (
          <p role="alert" className="text-sm text-error-secondary">
            {error}
          </p>
        )}
        <div>
          <Button type="submit" size="sm" disabled={pending}>
            {pending
              ? "Starting Design QA review…"
              : auth === "dummy"
                ? "Create dummy-auth review"
                : "Check public preview capture"}
          </Button>
        </div>
      </form>
    </Card>
  )
}

export function FrontendReportView({
  run,
  currentHeadSha,
}: {
  run: FrontendReviewRun
  currentHeadSha?: string | null
}) {
  const [scenarioId, setScenarioId] = useState(run.report?.scenarios[0]?.id)
  const [changesOnly, setChangesOnly] = useState(false)
  const report = run.report
  const scenarios = report?.scenarios ?? []
  const changesAndIncomplete = scenarios.filter(
    (item) =>
      item.status !== "complete" ||
      !item.before_url ||
      !item.after_url ||
      !item.metrics ||
      item.metrics.total_pixels === 0 ||
      item.metrics.changed_pixels > 0
  )
  const visibleScenarios = changesOnly ? changesAndIncomplete : scenarios
  const scenario =
    visibleScenarios.find((item) => item.id === scenarioId) ??
    visibleScenarios[0]
  const complete =
    report?.scenarios.filter(
      (item) => item.status === "complete" && item.before_url && item.after_url
    ).length ?? 0
  const statusColor =
    run.status === "failed"
      ? "error"
      : run.status === "blocked"
        ? "warning"
        : run.status === "complete"
          ? "success"
          : "secondary"
  return (
    <div className="flex flex-col gap-space-4">
      {currentHeadSha && currentHeadSha !== run.head_sha && (
        <Card role="status" className="p-space-4">
          <h3 className="text-sm font-medium text-warning-secondary">
            New PR changes have not been reviewed
          </h3>
          <p className="mt-space-2 text-sm text-secondary">
            This report covers commit{" "}
            <span className="font-mono">{run.head_sha.slice(0, 12)}</span>. The
            current PR commit is{" "}
            <span className="font-mono">{currentHeadSha.slice(0, 12)}</span>.
            Start a new Design QA review to check the latest changes.
          </p>
        </Card>
      )}
      <Card className="flex flex-col gap-space-3 p-space-4">
        <div className="flex flex-wrap items-center gap-space-2">
          <Badge color={statusColor} size="sm">
            {run.status}
          </Badge>
          <h3 className="text-sm font-medium">
            {run.status === "running" || run.status === "pending"
              ? run.status === "pending"
                ? "Frontend review queued…"
                : "Capturing paired preview evidence…"
              : report
                ? "Review evidence"
                : "No visual evidence captured"}
          </h3>
        </div>
        {report?.summary && (
          <p className="text-sm whitespace-pre-wrap text-secondary">
            {report.summary}
          </p>
        )}
        {run.error && (
          <p role="status" className="text-sm text-secondary">
            {run.error}
          </p>
        )}
        <div className="flex flex-wrap gap-x-space-5 gap-y-space-2 text-xs text-secondary">
          <span>
            Coverage: {complete}/{run.paths.length} routes captured
          </span>
          <span>
            AI review:{" "}
            {report?.ai_review_status === "complete"
              ? "Complete"
              : report?.ai_review_status === "running"
                ? "Reviewing screenshots…"
                : report?.ai_review_status === "failed"
                  ? "Failed"
                  : "Not run"}
          </span>
          <span>
            Build verification:{" "}
            {report?.build_verification === "verified"
              ? "Verified"
              : "Unverified"}
          </span>
          <span>
            Videos:{" "}
            {report?.video_status === "recorded" ? "Recorded" : "Not captured"}
          </span>
        </div>
        {report?.assessment_error && (
          <p role="status" className="text-sm text-warning-secondary">
            AI review: {report.assessment_error}. Captured evidence is still
            available.
          </p>
        )}
        {report?.assessment_thread_id && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: report.assessment_thread_id }}
            className="text-xs text-secondary underline underline-offset-2"
          >
            Open AI review conversation
          </Link>
        )}
        <div className="grid gap-space-2 border-t border-default pt-space-3 text-xs sm:grid-cols-2">
          <p className="min-w-0 truncate text-secondary" title={run.base_url}>
            Base{" "}
            <span className="font-mono text-primary">
              {run.base_sha.slice(0, 12)}
            </span>{" "}
            · {run.base_url}
          </p>
          <p className="min-w-0 truncate text-secondary" title={run.head_url}>
            PR{" "}
            <span className="font-mono text-primary">
              {run.head_sha.slice(0, 12)}
            </span>{" "}
            · {run.head_url}
          </p>
        </div>
      </Card>
      {run.status === "blocked" && (
        <Card className="p-space-4">
          <h3 className="text-sm font-medium">
            {run.auth_mode === "dummy"
              ? "Authentication required"
              : "Capture blocked"}
          </h3>
          <p className="mt-space-2 text-sm text-secondary">
            {run.auth_mode === "dummy"
              ? "Dummy login is intentionally unable to sign in. No preview was contacted and no page was reviewed. A private LangSmith preview needs a disposable QA identity before authenticated capture can run."
              : "The preview could not be captured. Blocked captures do not count as visually reviewed coverage."}
          </p>
        </Card>
      )}
      {report && report.build_verification !== "verified" && complete > 0 && (
        <p className="text-xs text-warning-secondary">
          Preview URLs have not been attested to these commits. Treat this as
          captured evidence, not verified PR/base coverage.
        </p>
      )}
      {report && (report.scenarios.length > 1 || changesOnly) && (
        <div className="flex flex-col gap-space-3">
          <div className="flex flex-wrap items-center gap-space-3">
            <Checkbox
              label="Changes and incomplete"
              checked={changesOnly}
              onCheckedChange={(checked) => setChangesOnly(checked === true)}
            />
            <p role="status" className="text-xs text-secondary">
              Showing {visibleScenarios.length}/{scenarios.length} routes
            </p>
          </div>
          <div
            className="flex flex-wrap gap-space-2"
            aria-label="Captured routes"
          >
            {visibleScenarios.map((item) => {
              const recordedChange =
                item.metrics && item.metrics.total_pixels > 0
                  ? `${(item.metrics.changed_ratio * 100).toFixed(2)}% recorded change`
                  : "Not compared"
              return (
                <Button
                  key={item.id}
                  color="secondary"
                  variant={item.id === scenario?.id ? "outlined" : "plain"}
                  size="sm"
                  onClick={() => setScenarioId(item.id)}
                  aria-pressed={item.id === scenario?.id}
                  aria-label={`${item.title} · ${item.status} · ${recordedChange}`}
                >
                  {item.title} · {item.status} · {recordedChange}
                </Button>
              )
            })}
          </div>
          <p className="text-xs text-secondary">
            Route percentages use the saved capture settings. Viewer settings
            apply only to the open screenshot comparison.
          </p>
          {visibleScenarios.length === 0 && (
            <Card role="status" className="p-space-4 text-sm text-secondary">
              No recorded changes. Clear the filter to inspect unchanged routes.
            </Card>
          )}
        </div>
      )}
      {scenario && (
        <section aria-label={scenario.title} className="min-w-0">
          <div className="mb-space-3 flex items-baseline gap-space-2">
            <h3 className="text-sm font-medium">{scenario.title}</h3>
            <span className="text-xs text-secondary">{scenario.path}</span>
          </div>
          <EvidenceViewer
            key={`${run.id}:${scenario.id}`}
            scenario={scenario}
          />
        </section>
      )}
    </div>
  )
}

function ErrorCard({ message }: { message: string }) {
  return (
    <Card role="alert" className="p-space-4 text-sm text-error-secondary">
      Could not load Design QA review: {message}. Refresh to retry.
    </Card>
  )
}
