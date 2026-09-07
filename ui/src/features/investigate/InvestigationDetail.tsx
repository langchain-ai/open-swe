import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import {
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCheck,
  CircleHelp,
  Clock3,
  FileSearch,
  Hash,
  LoaderCircle,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
} from "lucide-react"
import { useRef, useState } from "react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { investigateApi } from "./api"
import type { InvestigationAction } from "./api"
import {
  CitedText,
  ErrorState,
  ExternalLink,
  formatTime,
  humanize,
  LoadingState,
  StatusBadge,
} from "./shared"

const actions = [
  { action: "investigate_again", label: "Investigate again", icon: RefreshCw },
  { action: "pause", label: "Pause", icon: Pause },
  { action: "resume", label: "Resume", icon: Play },
  { action: "complete", label: "Complete", icon: CheckCheck },
  { action: "reopen", label: "Reopen", icon: RotateCcw },
] as const

const notices: Record<InvestigationAction, string> = {
  ask: "Question submitted. The answer will appear after the investigation pass.",
  investigate_again: "Another investigation pass requested.",
  pause: "Pause requested. Watching stops when the request is processed.",
  resume: "Resume requested. Watching resumes after eligibility is verified.",
  complete:
    "Completion requested. Watching stops when the request is processed.",
  reopen: "Reopen requested. Watching resumes after eligibility is verified.",
}

const appliedStates: Partial<Record<InvestigationAction, string[]>> = {
  pause: ["paused"],
  complete: ["completed"],
  resume: ["pending", "investigating", "watching"],
  reopen: ["pending", "investigating", "watching"],
}

function Section({
  title,
  aside,
  children,
}: {
  title: string
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
        <h2 className="text-sm font-medium">{title}</h2>
        {aside}
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}

export function InvestigationDetail({
  investigationId,
}: {
  investigationId: string
}) {
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState("")
  const [notice, setNotice] = useState<InvestigationAction | null>(null)
  const requestIdentity = useRef<{ key: string; id: string } | null>(null)
  const detail = useQuery({
    queryKey: ["investigate", "detail", investigationId],
    queryFn: () => investigateApi.detail(investigationId),
    refetchInterval: 5000,
    retry: false,
  })
  const command = useMutation({
    mutationFn: ({
      action,
      text,
    }: {
      action: InvestigationAction
      text?: string
    }) => {
      const key = JSON.stringify({ action, text })
      if (requestIdentity.current?.key !== key)
        requestIdentity.current = { key, id: crypto.randomUUID() }
      return investigateApi.command(
        investigationId,
        action,
        requestIdentity.current.id,
        text
      )
    },
    onSuccess: (_result, variables) => {
      setNotice(variables.action)
      requestIdentity.current = null
      if (variables.action === "ask") setQuestion("")
      void queryClient.invalidateQueries({ queryKey: ["investigate"] })
    },
  })
  if (detail.isPending) return <LoadingState />
  if (detail.error)
    return (
      <div className="mx-auto w-full max-w-5xl p-6">
        <ErrorState error={detail.error} retry={() => void detail.refetch()} />
      </div>
    )
  const { investigation, report, activity, allowed_actions, trace_url } =
    detail.data
  const gaps = [
    ...new Set([...(detail.data.coverage.gaps ?? []), ...(report?.gaps ?? [])]),
  ]
  const pendingTransition =
    notice &&
    appliedStates[notice] &&
    !appliedStates[notice]?.includes(investigation.status)

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 sm:px-10">
      <Link
        to="/investigate"
        className="mb-6 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        All investigations
      </Link>
      <header className="mb-6">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Hash className="size-3.5" />
          {investigation.channel_name}
          <span>·</span>
          <span>
            {investigation.is_archived ? "Channel archived" : "Channel open"}
          </span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <h1 className="max-w-3xl text-2xl font-semibold tracking-tight">
            {investigation.title || investigation.channel_name}
          </h1>
          <StatusBadge status={investigation.status} />
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <span>Updated {formatTime(investigation.updated_at)}</span>
        </div>
        {investigation.reason && (
          <p className="mt-3 text-sm text-warning-foreground">
            {humanize(investigation.reason)}
          </p>
        )}
        <div className="mt-6 flex flex-wrap items-center gap-2">
          {actions
            .filter(({ action }) => allowed_actions.includes(action))
            .map(({ action, label, icon: Icon }) => (
              <Button
                key={action}
                size="sm"
                variant="outline"
                disabled={
                  command.isPending ||
                  Boolean(pendingTransition && notice === action)
                }
                onClick={() => command.mutate({ action })}
              >
                <Icon className="size-3.5" />
                {label}
              </Button>
            ))}
          <ExternalLink
            href={investigation.slack_url}
            className="ml-auto text-xs font-medium text-muted-foreground"
          >
            Open in Slack
          </ExternalLink>
          {trace_url && (
            <ExternalLink
              href={trace_url}
              className="text-xs font-medium text-muted-foreground"
            >
              Open trace
            </ExternalLink>
          )}
        </div>
      </header>
      {command.error && (
        <div
          role="alert"
          className="mb-5 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive-foreground"
        >
          {command.error.message}
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="mb-5 rounded-lg border border-info/20 bg-info/5 p-3 text-sm text-info-foreground"
        >
          {pendingTransition || !appliedStates[notice]
            ? notices[notice]
            : `Investigation status updated: ${humanize(investigation.status).toLowerCase()}.`}
        </div>
      )}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-5">
          <Section
            title="Current findings"
            aside={
              report && (
                <span className="text-[11px] text-muted-foreground">
                  {report.outcome === "inconclusive"
                    ? "Inconclusive"
                    : "Findings available"}
                </span>
              )
            }
          >
            {report ? (
              <>
                <p className="text-sm leading-7 whitespace-pre-wrap">
                  <CitedText text={report.summary} evidence={report.evidence} />
                </p>
                {report.impact && (
                  <div className="mt-5 rounded-lg bg-muted p-4">
                    <h3 className="mb-1 text-xs font-medium">
                      Observed impact
                    </h3>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground">
                      <CitedText
                        text={report.impact}
                        evidence={report.evidence}
                      />
                    </p>
                  </div>
                )}
                <p className="mt-4 text-[11px] text-muted-foreground">
                  Report updated {formatTime(report.created_at)}
                </p>
              </>
            ) : (
              <div className="py-7 text-center">
                <FileSearch className="mx-auto mb-3 size-6 text-muted-foreground" />
                <h3 className="text-sm font-medium">No findings yet</h3>
                <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">
                  Findings appear here after the first pass.
                </p>
              </div>
            )}
          </Section>
          {report && report.hypotheses.length > 0 && (
            <Section title="Working hypotheses">
              <div className="space-y-5">
                {report.hypotheses.map((hypothesis, index) => (
                  <div key={`${index}-${hypothesis.title}`}>
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-sm font-medium">
                        {hypothesis.title}
                      </h3>
                      <span
                        className={cn(
                          "rounded px-2 py-0.5 text-[11px]",
                          hypothesis.assessment === "supported"
                            ? "bg-success/10 text-success-foreground"
                            : hypothesis.assessment === "rejected"
                              ? "bg-muted text-muted-foreground"
                              : "bg-warning/10 text-warning-foreground"
                        )}
                      >
                        {humanize(hypothesis.assessment)}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {hypothesis.evidence_ids.map((id) => {
                        const evidenceIndex = report.evidence.findIndex(
                          (evidence) => evidence.id === id
                        )
                        return evidenceIndex >= 0 ? (
                          <a
                            key={id}
                            href={`#evidence-${encodeURIComponent(id)}`}
                            className="text-xs text-info-foreground hover:underline"
                          >
                            Evidence {evidenceIndex + 1}
                          </a>
                        ) : (
                          <span
                            key={id}
                            className="text-xs text-muted-foreground"
                          >
                            Evidence unavailable
                          </span>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          )}
          {report && (
            <Section
              title="Evidence"
              aside={
                <span className="text-xs text-muted-foreground">
                  {report.evidence.length} sources
                </span>
              }
            >
              {report.evidence.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No linked evidence has been collected.
                </p>
              ) : (
                <ol className="space-y-5">
                  {report.evidence.map((evidence, index) => (
                    <li
                      key={evidence.id}
                      id={`evidence-${encodeURIComponent(evidence.id)}`}
                      className="scroll-mt-6"
                    >
                      <div className="mb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span className="flex size-5 items-center justify-center rounded border border-border">
                          {index + 1}
                        </span>
                        <span>{evidence.source}</span>
                        <span>·</span>
                        <span>
                          Retrieved {formatTime(evidence.retrieved_at)}
                        </span>
                      </div>
                      <ExternalLink
                        href={evidence.url}
                        className="text-sm leading-relaxed"
                      >
                        {evidence.summary}
                      </ExternalLink>
                      {evidence.query && (
                        <details className="mt-2 text-xs text-muted-foreground">
                          <summary className="cursor-pointer">
                            View query
                          </summary>
                          <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono break-all whitespace-pre-wrap">
                            {evidence.query}
                          </pre>
                        </details>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </Section>
          )}
          {allowed_actions.includes("ask") && (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                if (question.trim() && !command.isPending)
                  command.mutate({ action: "ask", text: question.trim() })
              }}
              className="rounded-xl border border-border bg-card p-4"
            >
              <label
                htmlFor="investigate-question"
                className="mb-3 block text-sm font-medium"
              >
                Ask Investigate
              </label>
              <Textarea
                id="investigate-question"
                aria-label="Ask Investigate"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                disabled={command.isPending}
                maxLength={8000}
                placeholder="Ask a question or share new evidence…"
                className="min-h-24 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
              />
              <div className="mt-3 flex items-center justify-end gap-4">
                <Button
                  type="submit"
                  size="sm"
                  disabled={!question.trim() || command.isPending}
                >
                  {command.isPending && command.variables.action === "ask" ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <ArrowUp className="size-4" />
                  )}
                  Send question
                </Button>
              </div>
            </form>
          )}
        </div>
        <aside className="space-y-5">
          <Section title="Coverage & access">
            {gaps.length > 0 ? (
              <ul className="space-y-3">
                {gaps.map((gap) => (
                  <li
                    key={gap}
                    className="flex gap-2 text-xs leading-relaxed text-warning-foreground"
                  >
                    <CircleHelp className="mt-0.5 size-3.5 shrink-0" />
                    {gap}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {report
                  ? "No coverage gaps reported in this pass."
                  : "No coverage assessment yet."}
              </p>
            )}
          </Section>
          {report && report.checked.length > 0 && (
            <Section title="Checks performed">
              <ul className="space-y-3">
                {report.checked.map((check) => (
                  <li
                    key={check}
                    className="flex gap-2 text-xs leading-relaxed text-muted-foreground"
                  >
                    <Check className="mt-0.5 size-3.5 shrink-0 text-success-foreground" />
                    {check}
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {report && report.questions.length > 0 && (
            <Section title="Open questions">
              <ul className="list-disc space-y-3 pl-3 text-xs leading-relaxed text-muted-foreground">
                {report.questions.map((openQuestion) => (
                  <li key={openQuestion}>{openQuestion}</li>
                ))}
              </ul>
            </Section>
          )}
          <details open className="rounded-xl border border-border bg-card">
            <summary className="cursor-pointer px-5 py-4 text-sm font-medium">
              Agent activity{" "}
              <span className="ml-1 text-xs font-normal text-muted-foreground">
                {activity.length}
              </span>
            </summary>
            <div className="border-t border-border p-5">
              {activity.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No activity recorded yet.
                </p>
              ) : (
                <ol className="space-y-5">
                  {activity.map((item) => (
                    <li key={item.id} className="flex gap-2.5">
                      <Clock3 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                      <div>
                        <p className="text-xs leading-relaxed">
                          {item.summary || humanize(item.type)}
                        </p>
                        <time className="mt-1 block text-[11px] text-muted-foreground">
                          {formatTime(item.at)}
                        </time>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </details>
        </aside>
      </div>
    </div>
  )
}
