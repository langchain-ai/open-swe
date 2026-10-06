import type { ReactNode } from "react"

import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import { AlertTriangle, ArrowUpRight } from "@/components/glyphs"
import { cn } from "@/lib/utils"
import { ApiError } from "@/lib/api"
import type { IncidentReport, IncidentView, Timestamp } from "./api"
import { citationIndices, citationPreview } from "./citations"

export const incidentViews: Array<{
  value: IncidentView
  label: string
}> = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "all", label: "All" },
]

export function humanize(value: string) {
  const words = value.replaceAll("_", " ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function formatTime(value: Timestamp | null) {
  if (value === null) return "Not verified"
  const date = new Date(typeof value === "number" ? value * 1000 : value)
  if (!Number.isFinite(date.getTime())) return "Unknown time"
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

const STATUS_TONE: Record<string, "info" | "attention" | "neutral"> = {
  pending: "info",
  watching: "info",
  investigating: "info",
  needs_attention: "attention",
  paused: "neutral",
  completed: "neutral",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? "neutral"} dot>
      {humanize(status)}
    </Badge>
  )
}

export function LoadingState() {
  return (
    <Inline
      role="status"
      gap="sm"
      justify="center"
      className="w-full py-24 text-body text-ink-subtle"
    >
      <Spinner size="sm" />
      Loading incidents…
    </Inline>
  )
}

export function ErrorState({
  error,
  retry,
}: {
  error: Error
  retry: () => void
}) {
  return (
    <StateNotice
      tone="RISK"
      icon={AlertTriangle}
      title="Incidents could not be loaded"
      description={error.message}
      action={
        <Button variant="outline" size="compact" onClick={retry}>
          Try again
        </Button>
      }
    />
  )
}

export function ExternalLink({
  href,
  children,
  className,
  showIcon = true,
}: {
  href: string | null | undefined
  children: ReactNode
  className?: string
  showIcon?: boolean
}) {
  if (!href || !/^https?:\/\//i.test(href))
    return <span className={className}>{children}</span>
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1.5 hover:underline",
        className
      )}
    >
      {children}
      {showIcon && <Icon icon={ArrowUpRight} size="sm" />}
    </a>
  )
}

export function CitedText({
  text,
  evidence,
}: {
  text: string
  evidence: IncidentReport["evidence"]
}) {
  return text.split(/(\[[^\]\n]+\])/g).map((part, index) => {
    const indices = citationIndices(part, evidence)
    return indices.length ? (
      <sup key={index}>
        {indices.map((evidenceIndex) => {
          const source = evidence[evidenceIndex]!
          return (
            <ExternalLink
              key={source.id}
              href={source.url}
              showIcon={false}
              className="mx-0.5 text-meta font-medium text-info"
            >
              <span
                aria-label={`Evidence ${evidenceIndex + 1}: ${source.source}`}
              >
                [{evidenceIndex + 1}]
              </span>
            </ExternalLink>
          )
        })}
      </sup>
    ) : part.startsWith("[") && !citationPreview(part) ? (
      <Box key={index} render={<span />} className="text-meta text-ink-subtle">
        [source unavailable]
      </Box>
    ) : (
      part
    )
  })
}

export function sourceLabel(summary: string, channelName: string) {
  return /^Slack message at [\d.]+; author .+\.$/.test(summary) ||
    summary === "Message in the incident channel"
    ? `Message in #${channelName}`
    : summary
}

export function slackMessageTime(url: string | null | undefined) {
  const timestamp = url?.match(/\/archives\/[^/]+\/p(\d{10})(\d{6})(?:[?#]|$)/)
  return timestamp ? Number(timestamp[1]) : null
}

export function isReadUnavailable(error: Error) {
  return !(error instanceof ApiError) || error.status >= 500
}
