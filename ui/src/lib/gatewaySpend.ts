import { queryOptions } from "@tanstack/react-query"

import { api, type GatewaySpendLimit } from "@/lib/api"

export const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

/**
 * A person's gateway spend caps. Spend changes outside Robin, so ready data
 * refreshes on focus; any other status stays until a manual refresh, since
 * each fetch makes an authorized LangSmith call.
 */
export const gatewaySpendQueryOptions = (userId: string) =>
  queryOptions({
    queryKey: ["gatewaySpend", userId],
    queryFn: api.gatewaySpend,
    staleTime: (query) =>
      query.state.data?.status === "ready" ? 60_000 : Infinity,
    refetchOnWindowFocus: true,
    retry: false,
  })

/** The cap window with the highest share used, which stops work first. */
export function tightestLimit(limits: Array<GatewaySpendLimit>) {
  return limits
    .flatMap((limit) => {
      if (limit.spend_usd === null) return []
      const used = Number(limit.spend_usd)
      const cap = Number(limit.limit_usd)
      if (!Number.isFinite(used) || !Number.isFinite(cap)) return []
      return [{ limit, used, cap, share: cap === 0 ? Infinity : used / cap }]
    })
    .reduce<{
      limit: GatewaySpendLimit
      used: number
      cap: number
      share: number
    } | null>(
      (best, next) => (best && best.share >= next.share ? best : next),
      null
    )
}
