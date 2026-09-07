import { ArrowUpRight, CircleAlert, LoaderCircle, Radar } from "lucide-react"
import type { ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { InvestigationReport, InvestigationView, Timestamp } from "./api"

export const investigationViews: Array<{
  value: InvestigationView
  label: string
}> = [
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "needs_attention", label: "Needs attention" },
  { value: "completed", label: "Completed" },
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
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        {
          "border-info/20 bg-info/5 text-info-foreground": [
            "pending",
            "watching",
            "investigating",
          ].includes(status),
          "border-warning/20 bg-warning/5 text-warning-foreground":
            status === "needs_attention",
          "border-border bg-muted text-muted-foreground": [
            "paused",
            "completed",
          ].includes(status),
        }
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full bg-current",
          status === "investigating" && "animate-pulse"
        )}
      />
      {humanize(status)}
    </span>
  )
}

export function LoadingState() {
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground"
    >
      <LoaderCircle className="size-4 animate-spin" />
      Loading investigations…
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
    <div
      role="alert"
      className="rounded-xl border border-destructive/20 bg-destructive/5 p-5"
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        <CircleAlert className="size-4 text-destructive" />
        Unable to load Investigate
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{error.message}</p>
      <Button variant="outline" size="sm" className="mt-4" onClick={retry}>
        Try again
      </Button>
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
      <ArrowUpRight className="size-3.5 shrink-0" />
    </a>
  )
}

export function CitedText({
  text,
  evidence,
}: {
  text: string
  evidence: InvestigationReport["evidence"]
}) {
  return text.split(/(\[[^\]\n]+\])/g).map((part, index) => {
    const evidenceIndex =
      part.startsWith("[") && part.endsWith("]")
        ? evidence.findIndex((item) => item.id === part.slice(1, -1))
        : -1
    const source = evidence[evidenceIndex]
    return source ? (
      <sup key={index}>
        <ExternalLink
          href={source.url}
          className="mx-0.5 text-[10px] font-medium text-info-foreground [&_svg]:hidden"
        >
          <span aria-label={`Evidence ${evidenceIndex + 1}: ${source.source}`}>
            [{evidenceIndex + 1}]
          </span>
        </ExternalLink>
      </sup>
    ) : (
      part
    )
  })
}

export function InvestigateMark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex size-9 items-center justify-center rounded-xl border border-info/20 bg-info/5 text-info-foreground",
        className
      )}
    >
      <Radar className="size-5" />
    </div>
  )
}
