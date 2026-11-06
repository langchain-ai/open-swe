import { Badge } from "@langchain/macaw-components/Badge"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { CircleIcon } from "@phosphor-icons/react/dist/ssr/Circle"
import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Spinner } from "@langchain/macaw-components/Spinner"
import type { ReactNode } from "react"
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

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      size="xxs"
      color={
        status === "needs_attention"
          ? "warning"
          : ["pending", "watching", "investigating"].includes(status)
            ? "primary"
            : "secondary"
      }
      leftDecorator={CircleIcon}
      iconWeight="fill"
      className={cn(
        "shrink-0",
        status === "investigating" && "[&_svg]:animate-pulse"
      )}
    >
      {humanize(status)}
    </Badge>
  )
}

export function LoadingState() {
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-space-2 py-24 text-sm text-secondary"
    >
      <Spinner size="xs" />
      Loading incidents…
    </div>
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
    <div role="alert">
      <Banner
        intent="error"
        title="Unable to load Incidents"
        action={
          <Button color="secondary" variant="outlined" onClick={retry}>
            Try again
          </Button>
        }
      >
        {error.message}
      </Banner>
    </div>
  )
}

export function ExternalLink({
  href,
  children,
  className,
}: {
  href: string | null | undefined
  children: ReactNode
  className?: string
}) {
  if (!href || !/^https?:\/\//i.test(href))
    return <span className={className}>{children}</span>
  return (
    <MacawLink
      variant="xs"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      rightDecorator={ArrowUpRightIcon}
      iconWeight="regular"
      className={className}
    >
      {children}
    </MacawLink>
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
              className="mx-0.5 text-xxs font-medium text-brand-primary [&_svg]:hidden"
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
      <span key={index} className="text-xs text-secondary">
        [source unavailable]
      </span>
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
