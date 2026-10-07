import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { FormField } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { RecordHeader } from "@langchain/gtm-platform-design-system/patterns/record-header"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ProviderMark } from "@langchain/gtm-platform-design-system/patterns/provider-mark"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@langchain/gtm-platform-design-system/ui/tabs"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import {
  Timeline,
  TimelineContent,
  TimelineDate,
  TimelineHeader,
  TimelineIndicator,
  TimelineItem,
  TimelineSeparator,
  TimelineTitle,
} from "@langchain/gtm-platform-design-system/ui/timeline"

import {
  AlertTriangle,
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCircle,
  ChevronDown,
  FileSearch,
  Hash,
  Info,
  Pause,
  Play,
  QuestionMarkCircle,
  RefreshCw,
  RotateCcw,
} from "@/components/glyphs"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { pageTitle } from "@/lib/pageTitle"
import { incidentsApi } from "./api"
import { IncidentDocuments } from "./IncidentDocuments"
import type { IncidentAction, IncidentReport } from "./api"
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
  { action: "complete", label: "Stop watching", icon: CheckCircle },
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

const ASSESSMENT_TONE: Record<
  IncidentReport["hypotheses"][number]["assessment"],
  "positive" | "attention" | "neutral"
> = {
  supported: "positive",
  plausible: "attention",
  rejected: "neutral",
}

/* Starts the rail and its dot on the first text line rather than 20px down. */
const TIMELINE_RAIL_CLASS = "group-data-[orientation=vertical]/timeline:top-1"
const SUBHEAD_CLASS = "text-label font-medium text-ink"
const PROSE_CLASS = "text-body whitespace-pre-wrap text-ink"

function Section({
  title,
  aside,
  inset = "padded",
  children,
}: {
  title: string
  aside?: ReactNode
  inset?: "padded" | "flush"
  children: ReactNode
}) {
  return (
    <PageSection title={title} actions={aside} contained inset={inset}>
      {children}
    </PageSection>
  )
}

function Count({ children }: { children: ReactNode }) {
  return (
    <Box render={<span />} className="text-meta text-ink-subtle">
      {children}
    </Box>
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
      <Box className="mx-auto w-full max-w-reading px-6 py-6 max-md:pt-16">
        <ErrorState error={detail.error} retry={() => void detail.refetch()} />
      </Box>
    )
  const { incident, report, activity, allowed_actions, trace_url } = detail.data
  const gaps = [
    ...new Set([
      ...(detail.data!.coverage.gaps ?? []),
      ...(report?.gaps ?? []),
    ]),
  ]
  const allowedCommands = actions.filter(({ action }) =>
    allowed_actions.includes(action)
  )
  const pendingTransition =
    notice &&
    appliedStates[notice] &&
    !appliedStates[notice]?.includes(incident.status)

  return (
    <ScrollArea overflow="vertical" className="h-full min-h-0 min-w-0 flex-1">
      <Stack
        gap="xl"
        className="mx-auto w-full max-w-work px-6 py-6 max-md:pt-16"
      >
        <Stack gap="sm">
          <Inline>
            <Link
              to="/incidents"
              className={buttonVariants({ variant: "ghost", size: "compact" })}
            >
              <Icon icon={ArrowLeft} size="sm" />
              All incidents
            </Link>
          </Inline>
          <RecordHeader
            title={
              <Box
                render={<h1 />}
                className="text-title font-semibold text-ink"
              >
                {incident.title || incident.channel_name}
              </Box>
            }
            status={<StatusBadge status={incident.status} />}
            actions={
              allowedCommands.length > 0
                ? allowedCommands.map(({ action, label, icon }) => (
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
                      <Icon icon={icon} size="sm" />
                      {label}
                    </Button>
                  ))
                : undefined
            }
            meta={
              <Stack gap="md">
                <Inline gap="md" wrap className="text-label text-ink-subtle">
                  <Inline render={<span />} gap="xs">
                    <Icon icon={Hash} size="sm" />
                    {incident.channel_name}
                  </Inline>
                  <span>
                    {incident.is_archived ? "Channel archived" : "Channel open"}
                  </span>
                  <span>Updated {formatTime(incident.updated_at)}</span>
                  <ExternalLink
                    href={incident.slack_url}
                    className="font-medium text-ink"
                  >
                    Open in Slack
                  </ExternalLink>
                  {trace_url && (
                    <ExternalLink
                      href={trace_url}
                      className="font-medium text-ink"
                    >
                      Open trace
                    </ExternalLink>
                  )}
                </Inline>
                {incident.reason && (
                  <Inline gap="sm" ink="attention" className="text-label">
                    <Icon icon={AlertTriangle} size="sm" />
                    {humanize(incident.reason)}
                  </Inline>
                )}
              </Stack>
            }
          />
        </Stack>

        {detail.error && (
          <StateNotice
            tone="INFO"
            icon={Info}
            title="Showing the last loaded incident"
            description={detail.error.message}
          />
        )}
        {notice && (
          <Alert tone="info" icon={Info} role="status">
            <AlertDescription>
              {pendingTransition || !appliedStates[notice]
                ? notices[notice]
                : `Agent activity updated: ${humanize(incident.status).toLowerCase()}.`}
            </AlertDescription>
          </Alert>
        )}

        <Tabs defaultValue="overview" className="gap-6">
          <TabsList variant="line" aria-label="Incident details">
            {(["overview", "postmortem", "timeline"] as const).map((tab) => (
              <TabsTrigger key={tab} value={tab}>
                {humanize(tab)}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent
            value="overview"
            keepMounted
            className="data-[hidden]:hidden"
          >
            <Box className="grid items-start gap-6 lg:grid-cols-3">
              <Stack gap="xl" className="min-w-0 lg:col-span-2">
                <Section
                  title="Latest finding"
                  aside={
                    report && (
                      <Count>
                        {report.outcome === "inconclusive"
                          ? "Inconclusive"
                          : "Findings available"}
                      </Count>
                    )
                  }
                >
                  {report ? (
                    <Stack gap="lg">
                      <Box render={<p />} className={PROSE_CLASS}>
                        <CitedText
                          text={report.summary}
                          evidence={report.evidence}
                        />
                      </Box>
                      {(
                        [
                          ["Problem", report.problem],
                          ["Previous occurrence", report.previous_occurrence],
                          ["Cause", report.cause],
                        ] as const
                      ).map(
                        ([label, value]) =>
                          value && (
                            <Stack key={label} gap="xs">
                              <Box render={<h3 />} className={SUBHEAD_CLASS}>
                                {label}
                              </Box>
                              <Box render={<p />} className={PROSE_CLASS}>
                                <CitedText
                                  text={value}
                                  evidence={report.evidence}
                                />
                              </Box>
                            </Stack>
                          )
                      )}
                      {Boolean(report.next_steps?.length) && (
                        <Alert tone="info" icon={Check}>
                          <Box render={<h3 />} className={SUBHEAD_CLASS}>
                            Steps to solve
                          </Box>
                          <Stack
                            render={<ul />}
                            gap="sm"
                            className="text-body text-ink"
                          >
                            {report.next_steps!.map((step, index) => (
                              <li key={index}>
                                <CitedText
                                  text={step}
                                  evidence={report.evidence}
                                />
                              </li>
                            ))}
                          </Stack>
                          <Box
                            render={<p />}
                            className="text-meta text-ink-subtle"
                          >
                            Recommendations for the responder
                          </Box>
                        </Alert>
                      )}
                      <Box render={<p />} className="text-meta text-ink-subtle">
                        Report updated {formatTime(report.created_at)}
                      </Box>
                    </Stack>
                  ) : (
                    <EmptyState
                      icon={FileSearch}
                      title="No findings yet"
                      description="Findings appear here after the first pass."
                    />
                  )}
                </Section>
                {report && (
                  <Section
                    title="Sources"
                    aside={<Count>{report.evidence.length} sources</Count>}
                    inset="flush"
                  >
                    {report.evidence.length === 0 ? (
                      <Box
                        render={<p />}
                        padding="lg"
                        className="text-body text-ink-subtle"
                      >
                        No linked evidence has been collected.
                      </Box>
                    ) : (
                      <Stack render={<ol />} gap="none">
                        {report.evidence.map((evidence, index) => (
                          <Inline
                            key={evidence.id}
                            render={<li />}
                            id={`evidence-${encodeURIComponent(evidence.id)}`}
                            gap="md"
                            align="start"
                            className="scroll-mt-6 border-b border-line p-3 last:border-b-0"
                          >
                            <ProviderMark
                              provider={evidence.source.toLowerCase()}
                              label={humanize(evidence.source)}
                            />
                            <Stack gap="xs" className="min-w-0 flex-1">
                              <Inline
                                gap="sm"
                                wrap
                                className="text-meta text-ink-subtle"
                              >
                                <Badge tier="chip">{index + 1}</Badge>
                                <Box
                                  render={<span />}
                                  className="font-medium text-ink"
                                >
                                  {humanize(evidence.source)}
                                </Box>
                                <span>
                                  {slackMessageTime(evidence.url)
                                    ? formatTime(slackMessageTime(evidence.url))
                                    : `Retrieved ${formatTime(evidence.retrieved_at)}`}
                                </span>
                              </Inline>
                              <ExternalLink
                                href={evidence.url}
                                className="text-body text-ink"
                              >
                                {sourceLabel(
                                  evidence.summary,
                                  incident.channel_name
                                )}
                              </ExternalLink>
                              {evidence.query && (
                                <Collapsible>
                                  <CollapsibleTrigger
                                    className={buttonVariants({
                                      variant: "ghost",
                                      size: "compact",
                                      className: "-ml-2.5 text-ink-subtle",
                                    })}
                                  >
                                    View query
                                    <Icon icon={ChevronDown} size="sm" />
                                  </CollapsibleTrigger>
                                  <CollapsibleContent>
                                    <Box
                                      render={<pre />}
                                      radius="badge"
                                      bg="muted"
                                      padding="md"
                                      className="mt-1 overflow-x-auto font-mono text-meta break-all whitespace-pre-wrap text-ink"
                                    >
                                      {evidence.query}
                                    </Box>
                                  </CollapsibleContent>
                                </Collapsible>
                              )}
                            </Stack>
                          </Inline>
                        ))}
                      </Stack>
                    )}
                  </Section>
                )}
                {report && report.hypotheses.length > 0 && (
                  <Section title="Working hypotheses">
                    <Stack gap="lg">
                      {report.hypotheses.map((hypothesis, index) => (
                        <Stack key={`${index}-${hypothesis.title}`} gap="sm">
                          <Inline gap="md" justify="between" align="start">
                            <Box render={<h3 />} className={SUBHEAD_CLASS}>
                              {hypothesis.title}
                            </Box>
                            <Badge
                              tone={ASSESSMENT_TONE[hypothesis.assessment]}
                            >
                              {humanize(hypothesis.assessment)}
                            </Badge>
                          </Inline>
                          <Inline gap="sm" wrap>
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
                                <Box
                                  key={id}
                                  render={<span />}
                                  className="text-meta text-ink-subtle"
                                >
                                  Evidence unavailable
                                </Box>
                              )
                            })}
                          </Inline>
                        </Stack>
                      ))}
                    </Stack>
                  </Section>
                )}
              </Stack>
              <Stack render={<aside />} gap="xl" className="min-w-0">
                {report?.impact && (
                  <Section title="Observed impact">
                    <Box render={<p />} className="text-body text-ink-subtle">
                      <CitedText
                        text={report.impact}
                        evidence={report.evidence}
                      />
                    </Box>
                  </Section>
                )}
                <Section title="Coverage & access">
                  {gaps.length > 0 ? (
                    <Stack render={<ul />} gap="md">
                      {gaps.map((gap) => (
                        <Inline
                          key={gap}
                          render={<li />}
                          gap="sm"
                          align="start"
                          ink="attention"
                          className="min-w-0 text-label wrap-anywhere"
                        >
                          <Icon
                            icon={QuestionMarkCircle}
                            size="sm"
                            className="mt-0.5"
                          />
                          {gap}
                        </Inline>
                      ))}
                    </Stack>
                  ) : (
                    <Box render={<p />} className="text-meta text-ink-subtle">
                      {report
                        ? "No coverage gaps reported in this pass."
                        : "No coverage assessment yet."}
                    </Box>
                  )}
                </Section>
                {report && report.checked.length > 0 && (
                  <Section title="Checks performed">
                    <Stack render={<ul />} gap="md">
                      {report.checked.map((check) => (
                        <Inline
                          key={check}
                          render={<li />}
                          gap="sm"
                          align="start"
                          className="text-label text-ink-subtle"
                        >
                          <Icon
                            icon={Check}
                            size="sm"
                            className="mt-0.5 text-positive"
                          />
                          {check}
                        </Inline>
                      ))}
                    </Stack>
                  </Section>
                )}
                {report && report.questions.length > 0 && (
                  <Section title="Open questions">
                    <Stack
                      render={<ul />}
                      gap="md"
                      className="list-disc pl-4 text-label text-ink-subtle"
                    >
                      {report.questions.map((openQuestion) => (
                        <li key={openQuestion}>{openQuestion}</li>
                      ))}
                    </Stack>
                  </Section>
                )}
              </Stack>
            </Box>
          </TabsContent>
          <TabsContent
            value="postmortem"
            keepMounted
            className="data-[hidden]:hidden"
          >
            <IncidentDocuments incidentId={incidentId} />
          </TabsContent>

          <TabsContent
            value="timeline"
            keepMounted
            className="data-[hidden]:hidden"
          >
            <Section
              title="Timeline"
              aside={<Count>{activity.length} events</Count>}
            >
              {activity.length === 0 ? (
                <Box render={<p />} className="text-body text-ink-subtle">
                  No activity recorded yet.
                </Box>
              ) : (
                <Timeline className="pl-2">
                  {[...activity]
                    .sort((a, b) => {
                      const timestamp = (value: typeof a.at) =>
                        new Date(
                          typeof value === "number" ? value * 1000 : value
                        ).getTime()
                      return timestamp(a.at) - timestamp(b.at)
                    })
                    .map((item, index) => (
                      <TimelineItem key={item.id} step={index + 1}>
                        <TimelineSeparator className={TIMELINE_RAIL_CLASS} />
                        <TimelineIndicator
                          className={TIMELINE_RAIL_CLASS}
                          connector={false}
                        />
                        <TimelineHeader>
                          <Inline gap="sm" align="baseline" wrap>
                            <TimelineDate className="tabular-nums">
                              {formatTime(item.at)}
                            </TimelineDate>
                            <TimelineTitle>{humanize(item.type)}</TimelineTitle>
                          </Inline>
                        </TimelineHeader>
                        <TimelineContent className="pt-1">
                          <Box render={<p />} className={PROSE_CLASS}>
                            <CitedText
                              text={item.summary || humanize(item.type)}
                              evidence={report?.evidence ?? []}
                            />
                          </Box>
                        </TimelineContent>
                      </TimelineItem>
                    ))}
                </Timeline>
              )}
            </Section>
          </TabsContent>
        </Tabs>

        {allowed_actions.includes("ask") && (
          <Stack
            render={
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  if (question.trim() && !command.isPending)
                    command.mutate({ action: "ask", text: question.trim() })
                }}
              />
            }
            gap="md"
            bg="panel"
            border="line"
            radius="panel"
            padding="lg"
          >
            <FormField
              label="Ask Open SWE"
              help="Shared with this incident"
              control={
                <Textarea
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  disabled={command.isPending}
                  maxLength={8000}
                  placeholder="Ask about a hypothesis, share context, or request a next step…"
                  className="min-h-24"
                />
              }
            />
            <Inline justify="end">
              <Button
                type="submit"
                size="compact"
                disabled={!question.trim() || command.isPending}
              >
                {command.isPending && command.variables.action === "ask" ? (
                  <Spinner size="sm" />
                ) : (
                  <Icon icon={ArrowUp} size="sm" />
                )}
                Send question
              </Button>
            </Inline>
          </Stack>
        )}
      </Stack>
    </ScrollArea>
  )
}
