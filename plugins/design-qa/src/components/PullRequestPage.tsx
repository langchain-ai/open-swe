import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Link } from "@langchain/macaw-components/Link"
import { Select } from "@langchain/macaw-components/Select"
import type { JsonObject, PullRequest } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { useEffect, useState } from "react"

import type { FrontendReviewRun, FrontendScenario } from "../lib/capture"
import {
  chatName,
  clip,
  MAX_LABEL,
  pullRequestContext,
  scenarioContext,
} from "../lib/ask"
import {
  pullRequestLabel,
  pullRequestUrl,
  repositoryOf,
  type PullRequestRef,
} from "../lib/pullRequest"
import {
  errorMessage,
  isForbidden,
  useCaptureChanges,
  useRun,
  useRuns,
  useTitle,
} from "../lib/queries"
import { useRouter } from "../lib/router"
import { ReportView } from "./ReportView"
import { ShareRepositories } from "./ShareRepositories"

/** One pull request's saved Design QA runs. */
export function PullRequestPage({
  pr,
  runId,
}: {
  pr: PullRequestRef
  runId: string | null
}) {
  const host = usePluginHost()
  const { navigate } = useRouter()
  const label = pullRequestLabel(pr)
  useTitle(clip(label, MAX_LABEL))
  const runs = useRuns(pr)
  useCaptureChanges(pr)
  const current = runs.data?.pull
  const selectedId = runId ?? runs.data?.runs[0]?.id
  const detail = useRun(pr, selectedId)
  const run = detail.data
  // The scenario last asked about, kept in the shared context until the person
  // asks about another or opens another run.
  const [asked, setAsked] = useState<{ run: string; scenario: string } | null>(
    null
  )
  const askedScenario =
    run && asked?.run === run.id
      ? run.report?.scenarios.find(({ id }) => id === asked.scenario)
      : undefined
  usePullRequestChat(pr, run, current, askedScenario)
  const empty = runs.isSuccess && !selectedId
  const options = (runs.data?.runs ?? []).map(runOption)
  if (run && !options.some((option) => option.value === run.id))
    options.push(runOption(run))

  return (
    <main className="mx-auto flex w-full max-w-[1500px] flex-col gap-space-5 p-space-5 max-sm:p-space-3">
      <header className="flex flex-col gap-space-3">
        <Button
          size="sm"
          color="secondary"
          variant="plain"
          className="self-start"
          leftDecorator={ArrowLeftIcon}
          onClick={() => navigate({ page: "inbox" })}
        >
          Design QA
        </Button>
        <div className="flex flex-wrap items-end justify-between gap-space-3">
          <div className="min-w-0">
            <p className="text-xs text-secondary">
              {label}
              {current?.draft ? " · Draft" : ""}
            </p>
            <h1 className="text-xl font-semibold tracking-tight text-primary">
              {current?.title ?? `Pull request #${pr.number}`}
            </h1>
          </div>
          <Link
            href={pullRequestUrl(pr)}
            variant="sm"
            rightDecorator={ArrowSquareOutIcon}
            onClick={(event) => {
              event.preventDefault()
              void host.nav.openExternal(pullRequestUrl(pr))
            }}
          >
            Open on GitHub
          </Link>
        </div>
      </header>
      {isForbidden(runs.error) || isForbidden(detail.error) ? (
        <ShareRepositories repository={repositoryOf(pr)} />
      ) : runs.isError ? (
        <ErrorCard message={errorMessage(runs.error)} />
      ) : detail.isError ? (
        <ErrorCard message={errorMessage(detail.error)} />
      ) : (
        <>
          {runs.isPending && (
            <p role="status" className="text-sm text-secondary">
              Loading Design QA reviews…
            </p>
          )}
          {empty && (
            <Card role="status" className="p-space-4 text-sm text-secondary">
              No saved comparisons. Live capture is unavailable until browser
              network isolation is in place.
            </Card>
          )}
          {options.length > 1 && (
            <label className="flex items-center gap-space-3 text-xs text-secondary">
              Review run
              <Select
                aria-label="Review run"
                size="md"
                triggerClassName="w-auto min-w-0 max-w-full"
                value={selectedId}
                onChange={(id) => {
                  if (id) navigate({ page: "pull", pr, run: id })
                }}
                options={options}
              />
            </label>
          )}
          {selectedId && detail.isPending && (
            <p role="status" className="text-sm text-secondary">
              Loading review evidence…
            </p>
          )}
          {run && (
            <ReportView
              key={run.id}
              pr={pr}
              run={run}
              currentHeadSha={current?.headSha}
              onAsk={(scenario) =>
                setAsked({ run: run.id, scenario: scenario.id })
              }
            />
          )}
        </>
      )}
    </main>
  )
}

const runOption = (run: FrontendReviewRun) => ({
  value: run.id,
  label: `${new Date(run.created_at).toLocaleString()} · ${run.status} · ${run.head_sha.slice(0, 7)}`,
})

/**
 * Once a run is readable, points the agent dock at this pull request's chat
 * and tells the agent which run, and which scenario of it the person asked
 * about, they have open with each message they send from it.
 */
function usePullRequestChat(
  pr: PullRequestRef,
  run: FrontendReviewRun | undefined,
  current: PullRequest | null | undefined,
  asked: FrontendScenario | undefined
) {
  const host = usePluginHost()
  const name = chatName(pr)
  const repository = repositoryOf(pr)
  const known = { title: current?.title, headSha: current?.headSha }
  const label = clip(
    asked ? `${pullRequestLabel(pr)} · ${asked.title}` : pullRequestLabel(pr),
    MAX_LABEL
  )
  const context = run
    ? JSON.stringify(
        asked
          ? scenarioContext(pr, run, asked, known)
          : pullRequestContext(pr, run, known)
      )
    : null
  useEffect(() => {
    if (!context) return
    void host.agent.use({ name, repository })
    void host.agent.share(JSON.parse(context) as JsonObject, { label })
  }, [host, name, repository, context, label])
}

function ErrorCard({ message }: { message: string }) {
  return (
    <Card role="alert" className="p-space-4 text-sm text-error-secondary">
      Could not load Design QA: {message}.
    </Card>
  )
}
