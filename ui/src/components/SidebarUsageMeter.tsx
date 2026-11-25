import { ProgressBar } from "@langchain/macaw-components/ProgressBar"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import {
  currency,
  gatewaySpendQueryOptions,
  tightestLimit,
} from "@/lib/gatewaySpend"

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
})

const resetDate = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
})

const SHORT_WINDOWS = new Set(["hourly", "daily"])

const resetTime = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
})

export function SidebarUsageMeter({
  userId,
  onNavigate,
}: {
  userId: string
  onNavigate?: () => void
}) {
  const { data } = useQuery(gatewaySpendQueryOptions(userId))
  const tightest = data?.status === "ready" ? tightestLimit(data.limits) : null
  if (!tightest) return null

  const { limit, used, cap } = tightest
  const reached = used >= cap
  const wholeCap = Number.isInteger(cap) && cap > 0
  const usedLabel = wholeCap
    ? dollars.format(reached ? used : Math.floor(used))
    : currency.format(used)
  const capLabel = wholeCap ? dollars.format(cap) : currency.format(cap)
  const resets = limit.resets_at ? new Date(limit.resets_at) : null
  const resetLabel = resets
    ? (SHORT_WINDOWS.has(limit.window) ? resetTime : resetDate).format(resets)
    : null

  return (
    <Link
      to="/usage"
      search={{ period: undefined }}
      onClick={onNavigate}
      aria-label={`${usedLabel} of ${capLabel} ${limit.window} spend cap${reached ? ", cap reached" : ""}${resetLabel ? `, resets ${resetLabel}` : ""}. Open Usage`}
      className="flex flex-col gap-space-1 rounded-md px-space-2 py-space-1 text-xxs hover:bg-sidebar-row-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
    >
      <span className="flex items-baseline justify-between gap-space-2">
        <span className="truncate tabular-nums">
          <span
            className={
              reached ? "text-error font-medium" : "font-medium text-primary"
            }
          >
            {usedLabel}
          </span>
          <span className="text-secondary"> / {capLabel}</span>
        </span>
        {resetLabel && (
          <span className="shrink-0 text-tertiary">
            {reached ? "Cap reached · " : ""}Resets{" "}
            <time dateTime={limit.resets_at ?? undefined}>{resetLabel}</time>
          </span>
        )}
      </span>
      <span aria-hidden>
        <ProgressBar
          aria-label={`${limit.name}, ${limit.window} spend`}
          value={cap === 0 ? 100 : Math.min(100, (used / cap) * 100)}
          color={reached ? "error" : "brand"}
          size="sm"
          className="h-0.5"
        />
      </span>
    </Link>
  )
}
