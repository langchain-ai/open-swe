import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import {
  TabGroup,
  TabLabel,
  TabList,
  TabPanel,
  TabPanels,
} from "@langchain/macaw-components/Tabs"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArrowUpIcon } from "@phosphor-icons/react/dist/ssr/ArrowUp"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { CheckIcon } from "@phosphor-icons/react/dist/ssr/Check"
import { ChecksIcon } from "@phosphor-icons/react/dist/ssr/Checks"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { FileMagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/FileMagnifyingGlass"
import { HashIcon } from "@phosphor-icons/react/dist/ssr/Hash"
import { PauseIcon } from "@phosphor-icons/react/dist/ssr/Pause"
import { PlayIcon } from "@phosphor-icons/react/dist/ssr/Play"
import { QuestionIcon } from "@phosphor-icons/react/dist/ssr/Question"
import { invalidationTopic } from "@/lib/invalidations/topics"
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
  {
    action: "investigate_again",
    label: "Investigate again",
    icon: ArrowsClockwiseIcon,
  },
  { action: "pause", label: "Pause", icon: PauseIcon },
  { action: "resume", label: "Resume", icon: PlayIcon },
  { action: "complete", label: "Stop watching", icon: ChecksIcon },
  { action: "reopen", label: "Watch again", icon: ArrowCounterClockwiseIcon },
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

const tabs = ["overview", "postmortem", "timeline"] as const

const assessmentColors = {
  supported: "success",
  plausible: "warning",
  rejected: "secondary",
} as const

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
    <section className="rounded-xl border border-default bg-surface-level-1">
      <div className="flex items-center justify-between gap-3 border-b border-default px-5 py-4">
        <h2 className="text-sm font-medium text-primary">{title}</h2>
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
        className="mb-6 inline-flex items-center gap-1.5 text-xs text-secondary hover:text-primary"
      >
        <ArrowLeftIcon size={14} weight="regular" />
        All incidents
      </Link>
      <header className="mb-6">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-secondary">
          <HashIcon size={14} weight="regular" />
          {incident.channel_name}
          <span>·</span>
          <span>
            {incident.is_archived ? "Channel archived" : "Channel open"}
          </span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <h1 className="max-w-3xl text-2xl font-semibold tracking-tight text-primary">
            {incident.title || incident.channel_name}
          </h1>
          <div className="flex items-center gap-2">
            <span className="text-xs text-secondary">Agent</span>
            <StatusBadge status={incident.status} />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-secondary">
          <span>Updated {formatTime(incident.updated_at)}</span>
        </div>
        {incident.reason && (
          <p className="mt-3 text-sm text-warning-secondary">
            {humanize(incident.reason)}
          </p>
        )}
        <div className="mt-6 flex flex-wrap items-center gap-2">
          {actions
            .filter(({ action }) => allowed_actions.includes(action))
            .map(({ action, label, icon }) => (
              <Button
                key={action}
                color="secondary"
                variant="outlined"
                leftDecorator={icon}
                disabled={
                  command.isPending ||
                  Boolean(pendingTransition && notice === action)
                }
                onClick={() => command.mutate({ action })}
              >
                {label}
              </Button>
            ))}
          <ExternalLink
            href={incident.slack_url}
            className="ml-auto text-xs font-medium text-secondary"
          >
            Open in Slack
          </ExternalLink>
          {trace_url && (
            <ExternalLink
              href={trace_url}
              className="text-xs font-medium text-secondary"
            >
              Open trace
            </ExternalLink>
          )}
        </div>
      </header>
      {detail.error && (
        <div role="alert" className="mb-5">
          <Banner intent="warning">{detail.error.message}</Banner>
        </div>
      )}
      {notice && (
        <div role="status" className="mb-5">
          <Banner intent="info">
            {pendingTransition || !appliedStates[notice]
              ? notices[notice]
              : `Agent activity updated: ${humanize(incident.status).toLowerCase()}.`}
          </Banner>
        </div>
      )}
      <TabGroup>
        <TabList className="mb-space-5 border-default">
          {tabs.map((tab) => (
            <TabLabel key={tab} label={humanize(tab)} className="pb-space-3" />
          ))}
        </TabList>
        <TabPanels>
          <TabPanel unmount={false}>
            <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
              <div className="min-w-0 space-y-5">
                <Section
                  title="Latest finding"
                  aside={
                    report && (
                      <span className="text-xxs text-secondary">
                        {report.outcome === "inconclusive"
                          ? "Inconclusive"
                          : "Findings available"}
                      </span>
                    )
                  }
                >
                  {report ? (
                    <>
                      <p className="text-base leading-7 whitespace-pre-wrap">
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
                              <h3 className="mb-1 text-xs font-medium text-secondary">
                                {label}
                              </h3>
                              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                                <CitedText
                                  text={value}
                                  evidence={report.evidence}
                                />
                              </p>
                            </div>
                          )
                      )}
                      {Boolean(report.next_steps?.length) && (
                        <div className="mt-5 rounded-lg border border-brand bg-brand-subtle p-4">
                          <h3 className="mb-2 text-xs font-medium">
                            Steps to solve
                          </h3>
                          <ul className="space-y-2 text-sm leading-relaxed">
                            {report.next_steps!.map((step, index) => (
                              <li key={index}>
                                <CitedText
                                  text={step}
                                  evidence={report.evidence}
                                />
                              </li>
                            ))}
                          </ul>
                          <p className="mt-3 text-xxs text-secondary">
                            Recommendations for the responder
                          </p>
                        </div>
                      )}
                      <p className="mt-4 text-xxs text-secondary">
                        Report updated {formatTime(report.created_at)}
                      </p>
                    </>
                  ) : (
                    <EmptyState
                      size="sm"
                      icon={FileMagnifyingGlassIcon}
                      title="No findings yet"
                      description="Findings appear here after the first pass."
                    />
                  )}
                </Section>
                {report && (
                  <Section
                    title="Sources"
                    aside={
                      <span className="text-xs text-secondary">
                        {report.evidence.length} sources
                      </span>
                    }
                  >
                    {report.evidence.length === 0 ? (
                      <p className="text-sm text-secondary">
                        No linked evidence has been collected.
                      </p>
                    ) : (
                      <ol className="space-y-3">
                        {report.evidence.map((evidence, index) => (
                          <li
                            key={evidence.id}
                            id={`evidence-${encodeURIComponent(evidence.id)}`}
                            className="scroll-mt-6 rounded-lg border border-default p-3"
                          >
                            <div className="mb-2 flex items-center gap-2 text-xxs text-secondary">
                              <span className="flex size-5 items-center justify-center rounded border border-default">
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
                              className="text-sm leading-relaxed"
                            >
                              {sourceLabel(
                                evidence.summary,
                                incident.channel_name
                              )}
                            </ExternalLink>
                            {evidence.query && (
                              <details className="mt-2 text-xs text-secondary">
                                <summary className="cursor-pointer">
                                  View query
                                </summary>
                                <pre className="mt-2 overflow-x-auto rounded-md bg-surface-level-2 p-3 font-mono break-all whitespace-pre-wrap">
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
                            <h3 className="text-sm font-medium">
                              {hypothesis.title}
                            </h3>
                            <Badge
                              size="xs"
                              rounded="xs"
                              color={assessmentColors[hypothesis.assessment]}
                            >
                              {humanize(hypothesis.assessment)}
                            </Badge>
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
                                  className="text-xs text-brand-primary hover:underline"
                                >
                                  Evidence {evidenceIndex + 1}
                                </a>
                              ) : (
                                <span
                                  key={id}
                                  className="text-xs text-secondary"
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
                    <p className="text-sm leading-relaxed text-secondary">
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
                          className="flex min-w-0 gap-2 text-xs leading-relaxed [overflow-wrap:anywhere] text-warning-secondary"
                        >
                          <QuestionIcon
                            size={14}
                            weight="regular"
                            className="mt-0.5 shrink-0"
                          />
                          {gap}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs leading-relaxed text-secondary">
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
                          className="flex gap-2 text-xs leading-relaxed text-secondary"
                        >
                          <CheckIcon
                            size={14}
                            weight="regular"
                            className="mt-0.5 shrink-0 text-icon-success"
                          />
                          {check}
                        </li>
                      ))}
                    </ul>
                  </Section>
                )}
                {report && report.questions.length > 0 && (
                  <Section title="Open questions">
                    <ul className="list-disc space-y-3 pl-3 text-xs leading-relaxed text-secondary">
                      {report.questions.map((openQuestion) => (
                        <li key={openQuestion}>{openQuestion}</li>
                      ))}
                    </ul>
                  </Section>
                )}
              </aside>
            </div>
          </TabPanel>
          <TabPanel unmount={false} className="space-y-5">
            <IncidentDocuments incidentId={incidentId} />
          </TabPanel>
          <TabPanel unmount={false}>
            <Section
              title="Timeline"
              aside={
                <span className="text-xs text-secondary">
                  {activity.length} events
                </span>
              }
            >
              {activity.length === 0 ? (
                <p className="text-sm text-secondary">
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
                        className="relative grid gap-2 border-b border-default py-5 last:border-0 sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-6"
                      >
                        <time className="text-xs text-secondary">
                          {formatTime(item.at)}
                        </time>
                        <div className="min-w-0">
                          <h3 className="mb-2 flex items-center gap-2 text-xs font-medium">
                            <ClockIcon
                              size={14}
                              weight="regular"
                              className="text-icon-secondary"
                            />
                            {humanize(item.type)}
                          </h3>
                          <p className="text-sm leading-relaxed whitespace-pre-wrap">
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
          </TabPanel>
        </TabPanels>
      </TabGroup>
      {allowed_actions.includes("ask") && (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (question.trim() && !command.isPending)
              command.mutate({ action: "ask", text: question.trim() })
          }}
          className="mt-6 rounded-xl border border-default bg-surface-level-1 p-4"
        >
          <Text
            as="label"
            htmlFor="incidents-question"
            variant="sm"
            weight="medium"
            className="mb-space-3 block"
          >
            Ask Open SWE
          </Text>
          <Textarea
            id="incidents-question"
            aria-label="Ask Open SWE"
            variant="plain"
            size="md"
            rows={4}
            value={question}
            onChange={setQuestion}
            disabled={command.isPending}
            maxLength={8000}
            placeholder="Ask about a hypothesis, share context, or request a next step…"
          />
          <div className="mt-3 flex items-center justify-between gap-4">
            <span className="text-xs text-secondary">
              Shared with this incident
            </span>
            <Button
              type="submit"
              leftDecorator={ArrowUpIcon}
              loading={command.isPending && command.variables.action === "ask"}
              disabled={!question.trim() || command.isPending}
            >
              Send question
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
