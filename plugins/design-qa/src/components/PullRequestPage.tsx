import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Link } from "@langchain/macaw-components/Link"
import { Select } from "@langchain/macaw-components/Select"
import type {
  FrontendReviewRun,
  JsonObject,
  OpenPullRequest,
} from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { useEffect } from "react"

import { chatName, pullRequestContext } from "../lib/ask"
import {
  pullRequestLabel,
  pullRequestUrl,
  repositoryOf,
  type PullRequestRef,
} from "../lib/pullRequest"
import {
  errorMessage,
  isForbidden,
  useListedPullRequest,
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
  useTitle(label)
  const listed = useListedPullRequest(pr)
  const runs = useRuns(pr)
  const selectedId = runId ?? runs.data?.[0]?.id
  const detail = useRun(pr, selectedId)
  const run = detail.data
  usePullRequestChat(pr, run, listed)
  const empty = runs.isSuccess && !selectedId
  const options = (runs.data ?? []).map(runOption)
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
              {listed?.draft ? " · Draft" : ""}
            </p>
            <h1 className="text-xl font-semibold tracking-tight text-primary">
              {listed?.title ?? `Pull request #${pr.number}`}
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
              currentHeadSha={listed?.headSha}
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
 * and tells the agent which run the person has open with each message they
 * send from it.
 */
function usePullRequestChat(
  pr: PullRequestRef,
  run: FrontendReviewRun | undefined,
  listed: OpenPullRequest | undefined
) {
  const host = usePluginHost()
  const name = chatName(pr)
  const repository = repositoryOf(pr)
  const label = pullRequestLabel(pr)
  const context = run
    ? JSON.stringify(pullRequestContext(pr, run, listed ?? {}))
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
