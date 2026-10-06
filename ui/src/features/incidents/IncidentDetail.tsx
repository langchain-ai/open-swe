import { Tabs } from "@base-ui/react/tabs"
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
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { cn } from "@/lib/utils"
import { pageTitle } from "@/lib/pageTitle"
import { incidentsApi } from "./api"
import { IncidentDocuments } from "./IncidentDocuments"
import type { IncidentAction } from "./api"
import {
  CitedText,
  ErrorState,
  ExternalLink,
  formatTime,
  humanize,
  isReadUnavailable,
  LoadingState,
  StatusBadge,
  sourceLabel,
  slackMessageTime,
} from "./shared"

const actions = [
  { action: "investigate_again", label: "Investigate again", icon: RefreshCw },
  { action: "pause", label: "Pause", icon: Pause },
  { action: "resume", label: "Resume", icon: Play },
  { action: "complete", label: "Stop watching", icon: CheckCheck },
  { action: "reopen", label: "Watch again", icon: RotateCcw },
] as const

const notices: Record<IncidentAction, string> = {
  ask: "Question submitted. The answer will appear after the incident pass.",
  investigate_again: "Another incident pass requested.",
  pause: "Pause requested. Watching stops when the request is processed.",
  resume: "Resume requested. Watching resumes after eligibility is verified.",
  complete:
    "Completion requested. Watching stops when the request is processed.",
  reopen: "Reopen requested. Watching resumes after eligibility is verified.",
}

const appliedStates: Partial<Record<IncidentAction, string[]>> = {
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
    <section className="rounded-control border border-line bg-panel">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
        <h2 className="text-body font-medium">{title}</h2>
        {aside}
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}

export function IncidentDetail({ incidentId }: { incidentId: string }) {
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState("")
  const [notice, setNotice] = useState<IncidentAction | null>(null)
  const requestIdentity = useRef<{ key: string; id: string } | null>(null)
  const detail = useQuery({
    queryKey: ["incidents", "detail", incidentId],
    queryFn: () => incidentsApi.detail(incidentId),
    meta: {
      invalidatedBy: [
        invalidationTopic("incidents", incidentId),
        invalidationTopic("incident-settings"),
      ],
    },
    retry: false,
  })
  const incidentTitle = detail.data?.incident.title
  const documentTitle = pageTitle(incidentTitle ?? "Incident")
  useEffect(() => {
    document.title = documentTitle
  }, [documentTitle])
  const command = useMutation({
    mutationFn: ({
      action,
      text,
    }: {
      action: IncidentAction
      text?: string
    }) => {
      const key = JSON.stringify({ action, text })
      if (requestIdentity.current?.key !== key)
        requestIdentity.current = { key, id: crypto.randomUUID() }
      return incidentsApi.command(
        incidentId,
        action,
        requestIdentity.current.id,
        text
      )
    },
    meta: { errorTitle: "Couldn't send incident request" },
    onSuccess: (_result, variables) => {
      setNotice(variables.action)
      requestIdentity.current = null
      if (variables.action === "ask") setQuestion("")
      void queryClient.invalidateQueries({ queryKey: ["incidents"] })
    },
  })
  if (detail.isPending) return <LoadingState />
  if (detail.error && (!detail.data || !isReadUnavailable(detail.error)))
    return (
      <div className="mx-auto w-full max-w-5xl p-6">
        <ErrorState error={detail.error} retry={() => void detail.refetch()} />
      </div>
    )
  const { incident, report, activity, allowed_actions, trace_url } = detail.data
  const gaps = [
    ...new Set([
      ...(detail.data!.coverage.gaps ?? []),
      ...(report?.gaps ?? []),
    ]),
  ]
  const pendingTransition =
    notice &&
    appliedStates[notice] &&
    !appliedStates[notice]?.includes(incident.status)

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 sm:px-10">
      <Link
        to="/incidents"
        className="mb-6 inline-flex items-center gap-1.5 text-meta text-ink-subtle hover:text-ink"
      >
        <ArrowLeft className="size-3.5" />
        All incidents
      </Link>
      <header className="mb-6">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-meta text-ink-subtle">
          <Hash className="size-3.5" />
          {incident.channel_name}
          <span>·</span>
          <span>
            {incident.is_archived ? "Channel archived" : "Channel open"}
          </span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <h1 className="max-w-3xl text-page font-semibold tracking-tightish">
            {incident.title || incident.channel_name}
          </h1>
          <div className="flex items-center gap-2">
            <span className="text-meta text-ink-subtle">Agent</span>
            <StatusBadge status={incident.status} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-meta text-ink-subtle">
          <span>Updated {formatTime(incident.updated_at)}</span>
        </div>
        {incident.reason && (
          <p className="mt-3 text-body text-attention">
            {humanize(incident.reason)}
          </p>
        )}
        <div className="mt-6 flex flex-wrap items-center gap-2">
          {actions
            .filter(({ action }) => allowed_actions.includes(action))
            .map(({ action, label, icon: Icon }) => (
              <Button
                key={action}
                size="compact"
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
            href={incident.slack_url}
            className="ml-auto text-meta font-medium text-ink-subtle"
          >
            Open in Slack
          </ExternalLink>
          {trace_url && (
            <ExternalLink
              href={trace_url}
              className="text-meta font-medium text-ink-subtle"
            >
              Open trace
            </ExternalLink>
          )}
        </div>
      </header>
      {detail.error && (
        <p role="alert" className="mb-5 text-body text-attention">
          {detail.error.message}
        </p>
      )}
      {notice && (
        <div
          role="status"
          className="mb-5 rounded-compact border border-info/20 bg-info/5 p-3 text-body text-info"
        >
          {pendingTransition || !appliedStates[notice]
            ? notices[notice]
            : `Agent activity updated: ${humanize(incident.status).toLowerCase()}.`}
        </div>
      )}
      <Tabs.Root defaultValue="overview">
        <Tabs.List
          aria-label="Incident details"
          className="mb-6 flex gap-6 border-b border-line"
        >
          {(["overview", "postmortem", "timeline"] as const).map((tab) => (
            <Tabs.Tab
              key={tab}
              value={tab}
              className="-mb-px border-b-2 border-transparent px-1 py-3 text-body text-ink-subtle outline-none focus-visible:ring-2 focus-visible:ring-primary data-[active]:border-ink data-[active]:font-medium data-[active]:text-ink"
            >
              {humanize(tab)}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        <Tabs.Panel
          value="overview"
          keepMounted
          className="data-[hidden]:hidden"
        >
          <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-w-0 space-y-5">
              <Section
                title="Latest finding"
                aside={
                  report && (
                    <span className="text-meta text-ink-subtle">
                      {report.outcome === "inconclusive"
                        ? "Inconclusive"
                        : "Findings available"}
                    </span>
                  )
                }
              >
                {report ? (
                  <>
                    <p className="text-title leading-7 whitespace-pre-wrap">
                      <CitedText
                        text={report.summary}
                        evidence={report.evidence}
                      />
                    </p>
                    {(
                      [
                        ["Problem", report.problem],
                        ["Previous occurrence", report.previous_occurrence],
                        ["Cause", report.cause],
                      ] as const
                    ).map(
                      ([label, value]) =>
                        value && (
                          <div key={label} className="mt-4">
                            <h3 className="mb-1 text-meta font-medium text-ink-subtle">
                              {label}
                            </h3>
                            <p className="text-body leading-relaxed whitespace-pre-wrap">
                              <CitedText
                                text={value}
                                evidence={report.evidence}
                              />
                            </p>
                          </div>
                        )
                    )}
                    {Boolean(report.next_steps?.length) && (
                      <div className="mt-5 rounded-compact border border-info/20 bg-info/5 p-4">
                        <h3 className="mb-2 text-label font-medium">
                          Steps to solve
                        </h3>
                        <ul className="space-y-2 text-body leading-relaxed">
                          {report.next_steps!.map((step, index) => (
                            <li key={index}>
                              <CitedText
                                text={step}
                                evidence={report.evidence}
                              />
                            </li>
                          ))}
                        </ul>
                        <p className="mt-3 text-meta text-ink-subtle">
                          Recommendations for the responder
                        </p>
                      </div>
                    )}
                    <p className="mt-4 text-meta text-ink-subtle">
                      Report updated {formatTime(report.created_at)}
                    </p>
                  </>
                ) : (
                  <div className="py-7 text-center">
                    <FileSearch className="mx-auto mb-3 size-6 text-ink-subtle" />
                    <h3 className="text-body font-medium">No findings yet</h3>
                    <p className="mx-auto mt-2 max-w-sm text-body leading-relaxed text-ink-subtle">
                      Findings appear here after the first pass.
                    </p>
                  </div>
                )}
              </Section>
              {report && (
                <Section
                  title="Sources"
                  aside={
                    <span className="text-meta text-ink-subtle">
                      {report.evidence.length} sources
                    </span>
                  }
                >
                  {report.evidence.length === 0 ? (
                    <p className="text-body text-ink-subtle">
                      No linked evidence has been collected.
                    </p>
                  ) : (
                    <ol className="space-y-3">
                      {report.evidence.map((evidence, index) => (
                        <li
                          key={evidence.id}
                          id={`evidence-${encodeURIComponent(evidence.id)}`}
                          className="scroll-mt-6 rounded-compact border border-line p-3"
                        >
                          <div className="mb-2 flex items-center gap-2 text-meta text-ink-subtle">
                            <span className="flex size-5 items-center justify-center rounded-tick border border-line">
                              {index + 1}
                            </span>
                            <span className="font-medium">
                              {humanize(evidence.source)}
                            </span>
                            <span>·</span>
                            <span>
                              {slackMessageTime(evidence.url)
                                ? formatTime(slackMessageTime(evidence.url))
                                : `Retrieved ${formatTime(evidence.retrieved_at)}`}
                            </span>
                          </div>
                          <ExternalLink
                            href={evidence.url}
                            className="text-body leading-relaxed"
                          >
                            {sourceLabel(
                              evidence.summary,
                              incident.channel_name
                            )}
                          </ExternalLink>
                          {evidence.query && (
                            <details className="mt-2 text-meta text-ink-subtle">
                              <summary className="cursor-pointer">
                                View query
                              </summary>
                              <pre className="mt-2 overflow-x-auto rounded-badge bg-muted p-3 font-mono break-all whitespace-pre-wrap">
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
              {report && report.hypotheses.length > 0 && (
                <Section title="Working hypotheses">
                  <div className="space-y-5">
                    {report.hypotheses.map((hypothesis, index) => (
                      <div key={`${index}-${hypothesis.title}`}>
                        <div className="flex items-start justify-between gap-3">
                          <h3 className="text-body font-medium">
                            {hypothesis.title}
                          </h3>
                          <span
                            className={cn(
                              "rounded-tick px-2 py-0.5 text-meta",
                              hypothesis.assessment === "supported"
                                ? "bg-positive-bg text-positive"
                                : hypothesis.assessment === "rejected"
                                  ? "bg-muted text-ink-subtle"
                                  : "bg-attention-bg text-attention"
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
                                className="text-label text-info hover:underline"
                              >
                                Evidence {evidenceIndex + 1}
                              </a>
                            ) : (
                              <span
                                key={id}
                                className="text-meta text-ink-subtle"
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
            </div>
            <aside className="min-w-0 space-y-5">
              {report?.impact && (
                <Section title="Observed impact">
                  <p className="text-body leading-relaxed text-ink-subtle">
                    <CitedText
                      text={report.impact}
                      evidence={report.evidence}
                    />
                  </p>
                </Section>
              )}
              <Section title="Coverage & access">
                {gaps.length > 0 ? (
                  <ul className="space-y-3">
                    {gaps.map((gap) => (
                      <li
                        key={gap}
                        className="flex min-w-0 gap-2 text-label leading-relaxed [overflow-wrap:anywhere] text-attention"
                      >
                        <CircleHelp className="mt-0.5 size-3.5 shrink-0" />
                        {gap}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-meta leading-relaxed text-ink-subtle">
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
                        className="flex gap-2 text-meta leading-relaxed text-ink-subtle"
                      >
                        <Check className="mt-0.5 size-3.5 shrink-0 text-positive" />
                        {check}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
              {report && report.questions.length > 0 && (
                <Section title="Open questions">
                  <ul className="list-disc space-y-3 pl-3 text-meta leading-relaxed text-ink-subtle">
                    {report.questions.map((openQuestion) => (
                      <li key={openQuestion}>{openQuestion}</li>
                    ))}
                  </ul>
                </Section>
              )}
            </aside>
          </div>
        </Tabs.Panel>
        <Tabs.Panel
          value="postmortem"
          keepMounted
          className="space-y-5 data-[hidden]:hidden"
        >
          <IncidentDocuments incidentId={incidentId} />
        </Tabs.Panel>

        <Tabs.Panel
          value="timeline"
          keepMounted
          className="data-[hidden]:hidden"
        >
          <Section
            title="Timeline"
            aside={
              <span className="text-meta text-ink-subtle">
                {activity.length} events
              </span>
            }
          >
            {activity.length === 0 ? (
              <p className="text-body text-ink-subtle">
                No activity recorded yet.
              </p>
            ) : (
              <ol className="space-y-0">
                {[...activity]
                  .sort((a, b) => {
                    const timestamp = (value: typeof a.at) =>
                      new Date(
                        typeof value === "number" ? value * 1000 : value
                      ).getTime()
                    return timestamp(a.at) - timestamp(b.at)
                  })
                  .map((item) => (
                    <li
                      key={item.id}
                      className="relative grid gap-2 border-b border-line py-5 last:border-0 sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-6"
                    >
                      <time className="text-meta text-ink-subtle">
                        {formatTime(item.at)}
                      </time>
                      <div className="min-w-0">
                        <h3 className="mb-2 flex items-center gap-2 text-label font-medium">
                          <Clock3 className="size-3.5 text-ink-subtle" />
                          {humanize(item.type)}
                        </h3>
                        <p className="text-body leading-relaxed whitespace-pre-wrap">
                          <CitedText
                            text={item.summary || humanize(item.type)}
                            evidence={report?.evidence ?? []}
                          />
                        </p>
                      </div>
                    </li>
                  ))}
              </ol>
            )}
          </Section>
        </Tabs.Panel>
      </Tabs.Root>
      {allowed_actions.includes("ask") && (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (question.trim() && !command.isPending)
              command.mutate({ action: "ask", text: question.trim() })
          }}
          className="mt-6 rounded-control border border-line bg-panel p-4"
        >
          <label
            htmlFor="incidents-question"
            className="mb-3 block text-body font-medium"
          >
            Ask Open SWE
          </label>
          <Textarea
            id="incidents-question"
            aria-label="Ask Open SWE"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={command.isPending}
            maxLength={8000}
            placeholder="Ask about a hypothesis, share context, or request a next step…"
            className="min-h-24 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
          />
          <div className="mt-3 flex items-center justify-between gap-4">
            <span className="text-meta text-ink-subtle">
              Shared with this incident
            </span>
            <Button
              type="submit"
              size="compact"
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
  )
}
