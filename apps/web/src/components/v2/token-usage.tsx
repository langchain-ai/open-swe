import { CacheMetrics } from "@open-swe/shared/open-swe/types";
import { calculateCostSavings } from "@open-swe/shared/caching";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { ChartNoAxesColumnIncreasing } from "lucide-react";

export function TokenUsage({ tokenData }: { tokenData?: CacheMetrics }) {
  if (!tokenData) return null;

  const metrics = calculateCostSavings(tokenData);
  return (
    <div className="mt-4 ml-auto flex">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>
            <ChartNoAxesColumnIncreasing />
          </TooltipTrigger>
          <TooltipContent className="flex w-full flex-col gap-1 text-sm">
            <p>Token usage data on actions where caching is enabled:</p>
            <span className="flex w-full items-center justify-between">
              <p>Input Tokens:</p>
              <p>{metrics.totalInputTokens.toLocaleString()}</p>
            </span>
            <span className="flex w-full items-center justify-between">
              <p>Output Tokens:</p>
              <p>{metrics.totalOutputTokens.toLocaleString()}</p>
            </span>
            <span className="flex w-full items-center justify-between">
              <p>Total Tokens:</p>
              <p>{metrics.totalTokens.toLocaleString()}</p>
            </span>

            <span className="flex w-full items-center justify-between">
              <p>Output Tokens Cost:</p>
              <p>${metrics.totalOutputTokensCost.toFixed(2)}</p>
            </span>
            <span className="flex w-full items-center justify-between">
              <p>Cache Savings:</p>
              <p>${metrics.totalSavings.toFixed(2)}</p>
            </span>
            <span className="flex w-full items-center justify-between">
              <p>Total Cost:</p>
              <p>${metrics.totalCost.toFixed(2)}</p>
            </span>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
