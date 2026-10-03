import { CheckCircleIcon, CircleIcon, XCircleIcon } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

export interface CheckLike {
  status: string
  conclusion: string | null
}

export type CheckOutcome = "failed" | "running" | "passed" | "skipped"

export function checkOutcome(check: CheckLike): CheckOutcome {
  if (check.status !== "completed") return "running"
  if (check.conclusion === "success") return "passed"
  if (check.conclusion === "neutral" || check.conclusion === "skipped")
    return "skipped"
  return "failed"
}

export const checkTones: Record<CheckOutcome, string> = {
  failed: "text-destructive",
  running: "text-warning-foreground",
  passed: "text-success-foreground",
  skipped: "text-muted-foreground",
}

export function CheckStatusIcon({
  check,
  className,
}: {
  check: CheckLike
  className?: string
}) {
  const outcome = checkOutcome(check)
  const classes = cn("size-3.5 shrink-0", checkTones[outcome], className)
  if (outcome === "running")
    return <CircleIcon aria-hidden className={cn(classes, "animate-pulse")} />
  if (outcome === "passed")
    return <CheckCircleIcon aria-hidden className={classes} />
  if (outcome === "skipped")
    return <CircleIcon aria-hidden className={classes} />
  return <XCircleIcon aria-hidden className={classes} />
}
