import { CacheMetrics } from "@open-swe/shared/open-swe/types";
import { calculateCostSavings } from "@open-swe/shared/caching";
import { Badge } from "../ui/badge";
import { Separator } from "../ui/separator";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "../ui/hover-card";
import {
  ChartNoAxesColumnIncreasing,
  Coins,
  TrendingUp,
  Zap,
} from "lucide-react";

interface TokenUsageProps {
  tokenData?: CacheMetrics[];
}

function mergeTokenData(tokenDataArray: CacheMetrics[]): CacheMetrics {
  return tokenDataArray.reduce(
    (merged, current) => ({
      cacheCreationInputTokens:
        merged.cacheCreationInputTokens + current.cacheCreationInputTokens,
      cacheReadInputTokens:
        merged.cacheReadInputTokens + current.cacheReadInputTokens,
      inputTokens: merged.inputTokens + current.inputTokens,
      outputTokens: merged.outputTokens + current.outputTokens,
    }),
    {
      cacheCreationInputTokens: 0,
      cacheReadInputTokens: 0,
      inputTokens: 0,
      outputTokens: 0,
    },
  );
}

export function TokenUsage({ tokenData }: TokenUsageProps) {
  if (!tokenData || tokenData.length === 0) return null;

  const mergedTokenData = mergeTokenData(tokenData);
  const metrics = calculateCostSavings(mergedTokenData);

  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <button className="hover:bg-muted/50 ml-auto flex items-center gap-2 rounded-md p-2 transition-colors">
          <ChartNoAxesColumnIncreasing className="h-4 w-4" />
          <Badge
            variant="secondary"
            className="text-xs"
          >
            {tokenData.length} agent{tokenData.length !== 1 ? "s" : ""}
          </Badge>
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="w-80">
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <ChartNoAxesColumnIncreasing className="h-4 w-4" />
            <h4 className="text-sm font-semibold">Token Usage</h4>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <div className="flex items-center gap-1.5">
                <Zap className="h-3 w-3 text-blue-500" />
                <span className="text-muted-foreground text-xs font-medium">
                  Input
                </span>
              </div>
              <p className="text-sm font-semibold">
                {metrics.totalInputTokens.toLocaleString()}
              </p>
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-1.5">
                <TrendingUp className="h-3 w-3 text-green-500" />
                <span className="text-muted-foreground text-xs font-medium">
                  Output
                </span>
              </div>
              <p className="text-sm font-semibold">
                {metrics.totalOutputTokens.toLocaleString()}
              </p>
            </div>
          </div>

          <Separator />

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-xs font-medium">
                Total Tokens
              </span>
              <span className="text-sm font-semibold">
                {metrics.totalTokens.toLocaleString()}
              </span>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <Coins className="h-3 w-3 text-amber-500" />
                  <span className="text-muted-foreground text-xs font-medium">
                    Cost
                  </span>
                </div>
                <span className="text-sm font-semibold">
                  ${metrics.totalCost.toFixed(2)}
                </span>
              </div>

              {metrics.totalSavings > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-green-600">
                    Cache Savings
                  </span>
                  <Badge
                    variant="outline"
                    className="border-green-200 text-green-600"
                  >
                    -${metrics.totalSavings.toFixed(2)}
                  </Badge>
                </div>
              )}
            </div>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
