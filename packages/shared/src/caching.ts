import { CacheMetrics } from "./open-swe/types.js";

export function calculateCostSavings(metrics: CacheMetrics): {
  totalSavings: number;
  totalCost: number;
  totalTokens: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalOutputTokensCost: number;
} {
  const SONNET_4_BASE_RATE = 3.0 / 1_000_000; // $3 per MTok
  const SONNET_4_OUTPUT_RATE = 15.0 / 1_000_000; // $15 per MTok

  const CACHE_WRITE_MULTIPLIER = 1.25;
  const CACHE_READ_MULTIPLIER = 0.1;

  const cacheWriteCost =
    metrics.cacheCreationInputTokens *
    SONNET_4_BASE_RATE *
    CACHE_WRITE_MULTIPLIER;

  const cacheReadCost =
    metrics.cacheReadInputTokens * SONNET_4_BASE_RATE * CACHE_READ_MULTIPLIER;

  const regularInputCost = metrics.inputTokens * SONNET_4_BASE_RATE;

  const totalOutputTokensCost = metrics.outputTokens * SONNET_4_OUTPUT_RATE;

  // Cost without caching (all tokens at base rate)
  const totalInputTokens =
    metrics.cacheCreationInputTokens +
    metrics.cacheReadInputTokens +
    metrics.inputTokens;
  const totalTokens = totalInputTokens + metrics.outputTokens;
  const costWithoutCaching = totalInputTokens * SONNET_4_BASE_RATE;

  // Actual cost with caching
  const actualCost = cacheWriteCost + cacheReadCost + regularInputCost;

  return {
    totalSavings: costWithoutCaching - actualCost,
    totalCost: actualCost,
    totalTokens,
    totalInputTokens,
    totalOutputTokens: metrics.outputTokens,
    totalOutputTokensCost,
  };
}
