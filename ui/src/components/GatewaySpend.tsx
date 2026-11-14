import { useQuery } from "@tanstack/react-query"
import { Card } from "@langchain/macaw-components/Card"
import { Button } from "@langchain/macaw-components/Button"
import { ProgressBar } from "@langchain/macaw-components/ProgressBar"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"

import { api } from "@/lib/api"

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

const resetTime = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
})

export function GatewaySpend({ userId }: { userId: string }) {
  const spend = useQuery({
    queryKey: ["gatewaySpend", userId],
    queryFn: api.gatewaySpend,
    staleTime: 60_000,
    retry: false,
  })
  const data = spend.data

  return (
    <Card className="my-space-5 flex flex-col gap-space-4">
      <div className="flex items-center justify-between gap-space-3">
        <Text variant="h3">Your Gateway Spend</Text>
        <Button
          variant="outlined"
          size="sm"
          disabled={spend.isFetching}
          onClick={() => void spend.refetch()}
        >
          Refresh
        </Button>
      </div>
      <Text variant="sm" color="secondary">
        Current spend against your personal LangSmith gateway caps, across
        gateway traffic—not just Robin. Each cap uses its own window,
        independent of the date range below. Shared workspace and organization
        limits may also apply.
      </Text>
      {spend.isPending ? (
        <Skeleton aria-label="Loading gateway spend" className="h-16 w-full" />
      ) : spend.isError || data?.status === "unavailable" ? (
        <Text role="status">Gateway spend is unavailable. Try refreshing.</Text>
      ) : data?.status === "sign_in_required" ? (
        <Text role="status">
          Sign in to Robin with LangSmith again to see your gateway spend.
        </Text>
      ) : data?.status === "forbidden" ? (
        <Text role="status">
          Your LangSmith account cannot read gateway policies, or the gateway is
          not enabled for your organization.
        </Text>
      ) : data?.limits.length === 0 ? (
        <Text>
          No active user-only spend caps were found. This does not mean your
          gateway usage is unlimited.
        </Text>
      ) : (
        <div className="grid gap-space-5 sm:grid-cols-2">
          {data?.limits.map((limit) => {
            const cap = Number(limit.limit_usd)
            const used =
              limit.spend_usd === null ? null : Number(limit.spend_usd)
            const reached = used !== null && used >= cap
            return (
              <div
                key={`${limit.policy_id}:${limit.window}:${limit.providers.join(",")}`}
                className="flex flex-col gap-space-2"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-space-2">
                  <Text variant="sm" weight="medium">
                    {limit.name}
                  </Text>
                  <Text variant="sm" color="secondary">
                    {limit.window}
                    {limit.providers.length > 0
                      ? ` · ${limit.providers.join(", ")} (BYOK)`
                      : ""}
                  </Text>
                </div>
                <Text>
                  <span className="font-medium tabular-nums">
                    {used === null
                      ? "Spend unavailable"
                      : currency.format(used)}
                  </span>
                  {" / "}
                  {currency.format(cap)} cap
                </Text>
                {used !== null && (
                  <ProgressBar
                    aria-label={`${limit.name}, ${limit.window} spend`}
                    value={cap === 0 ? 100 : Math.min(100, (used / cap) * 100)}
                    color={reached ? "error" : "brand"}
                    size="sm"
                  />
                )}
                {limit.resets_at && (
                  <Text variant="sm" color="secondary">
                    Resets{" "}
                    <time dateTime={limit.resets_at}>
                      {resetTime.format(new Date(limit.resets_at))}
                    </time>
                  </Text>
                )}
                {reached && (
                  <Text variant="sm" color="error">
                    Cap reached
                  </Text>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
