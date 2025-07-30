import { CacheMetrics, ModelTokenData } from "@open-swe/shared/open-swe/types";
import {
  calculateCostSavings,
  tokenDataReducer,
} from "@open-swe/shared/caching";
import { Badge } from "../ui/badge";
import { Separator } from "../ui/separator";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "../ui/hover-card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../ui/collapsible";
import {
  ChartNoAxesColumnIncreasing,
  ChevronDown,
  ChevronRight,
  Coins,
  TrendingUp,
  Zap,
} from "lucide-react";
import { useState } from "react";

interface TokenUsageProps {
  tokenData?: ModelTokenData[] | CacheMetrics[];
}

function isModelTokenData(
  data: ModelTokenData[] | CacheMetrics[],
): data is ModelTokenData[] {
  return data.length > 0 && "model" in data[0];
}

function mergeTokenData(
  tokenDataArray: ModelTokenData[] | CacheMetrics[],
): CacheMetrics {
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

function mergeModelTokenData(tokenData: ModelTokenData[]): ModelTokenData[] {
  if (tokenData.length <= 1) {
    return tokenData;
  }
  const [firstTokenData, ...restTokenData] = tokenData;
  return tokenDataReducer([firstTokenData], restTokenData);
}

function getModelPricingPlaceholder(model: string): {
  inputPrice: number;
  outputPrice: number;
  cachePrice: number;
} {
  // Actual Claude pricing per 1M tokens based on official pricing table
  const pricingMap: Record<
    string,
    { inputPrice: number; outputPrice: number; cachePrice: number }
  > = {
    // Claude 4 models
    "anthropic:claude-4-opus": {
      inputPrice: 15.0,
      outputPrice: 75.0,
      cachePrice: 1.5,
    },
    "anthropic:claude-4-sonnet": {
      inputPrice: 3.0,
      outputPrice: 15.0,
      cachePrice: 0.3,
    },

    // Claude 3.7 models
    "anthropic:claude-3-7-sonnet": {
      inputPrice: 3.0,
      outputPrice: 15.0,
      cachePrice: 0.3,
    },

    // Claude 3.5 models
    "anthropic:claude-3-5-sonnet-20241022": {
      inputPrice: 3.0,
      outputPrice: 15.0,
      cachePrice: 0.3,
    },
    "anthropic:claude-3-5-sonnet-20240620": {
      inputPrice: 3.0,
      outputPrice: 15.0,
      cachePrice: 0.3,
    },
    "anthropic:claude-3-5-haiku-20241022": {
      inputPrice: 0.8,
      outputPrice: 4.0,
      cachePrice: 0.08,
    },

    // Claude 3 models
    "anthropic:claude-3-opus-20240229": {
      inputPrice: 15.0,
      outputPrice: 75.0,
      cachePrice: 1.5,
    },
    "anthropic:claude-3-haiku-20240307": {
      inputPrice: 0.25,
      outputPrice: 1.25,
      cachePrice: 0.03,
    },

    // OpenAI models (actual pricing - no caching support)
    "openai:o4": { inputPrice: 1.1, outputPrice: 4.4, cachePrice: 1.1 },
    "openai:o4-mini": { inputPrice: 1.1, outputPrice: 4.4, cachePrice: 1.1 },
    "openai:o3": { inputPrice: 2.0, outputPrice: 8.0, cachePrice: 2.0 },
    "openai:o3-mini": { inputPrice: 1.1, outputPrice: 4.4, cachePrice: 1.1 },
    "openai:gpt-4o": { inputPrice: 2.5, outputPrice: 10.0, cachePrice: 2.5 },
    "openai:gpt-4o-mini": {
      inputPrice: 0.15,
      outputPrice: 0.6,
      cachePrice: 0.15,
    },
    "openai:gpt-4.1": { inputPrice: 2.0, outputPrice: 8.0, cachePrice: 2.0 },
    "openai:gpt-4.1-mini": {
      inputPrice: 0.4,
      outputPrice: 1.6,
      cachePrice: 0.4,
    },
    // Legacy OpenAI models
    "openai:o1-preview": {
      inputPrice: 15.0,
      outputPrice: 60.0,
      cachePrice: 15.0,
    },
    "openai:o1-mini": { inputPrice: 3.0, outputPrice: 12.0, cachePrice: 3.0 },

    // Google Gemini models (actual pricing - no caching support)
    "google-genai:gemini-2.5-pro": {
      inputPrice: 1.5,
      outputPrice: 10.0,
      cachePrice: 1.5,
    },
    "google-genai:gemini-2.5-flash": {
      inputPrice: 0.3,
      outputPrice: 2.5,
      cachePrice: 0.3,
    },
  };

  return (
    pricingMap[model] || { inputPrice: 1.0, outputPrice: 5.0, cachePrice: 0.5 }
  ); // Default fallback
}

function calculateModelCost(modelData: ModelTokenData): number {
  const pricing = getModelPricingPlaceholder(modelData.model);
  const baseInputCost =
    (modelData.inputTokens * pricing.inputPrice) / 1_000_000;
  const cacheCreationCost =
    (modelData.cacheCreationInputTokens * pricing.inputPrice) / 1_000_000; // Cache creation uses base input price
  const cacheHitCost =
    (modelData.cacheReadInputTokens * pricing.cachePrice) / 1_000_000; // Cache hits use discounted price
  const outputCost = (modelData.outputTokens * pricing.outputPrice) / 1_000_000;
  return baseInputCost + cacheCreationCost + cacheHitCost + outputCost;
}

export function TokenUsage({ tokenData }: TokenUsageProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!tokenData || tokenData.length === 0) return null;

  const mergedTokenData = mergeTokenData(tokenData);
  const totalCachedInputTokens =
    mergedTokenData.cacheCreationInputTokens +
    mergedTokenData.cacheReadInputTokens;
  const totalUncachedInputTokens = mergedTokenData.inputTokens;
  const cachePercentage = (
    (totalCachedInputTokens /
      (totalCachedInputTokens + totalUncachedInputTokens)) *
    100
  ).toFixed(2);
  const metrics = calculateCostSavings(mergedTokenData);

  const hasModelData = isModelTokenData(tokenData);
  const modelTokenData = hasModelData
    ? mergeModelTokenData(tokenData as ModelTokenData[])
    : [];

  // Calculate total cost using model-specific pricing if available
  const totalModelCost = hasModelData
    ? modelTokenData.reduce((sum, model) => sum + calculateModelCost(model), 0)
    : metrics.totalCost;

  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <button className="hover:bg-muted/50 ml-auto flex items-center gap-2 rounded-md p-2 transition-colors">
          <ChartNoAxesColumnIncreasing className="h-4 w-4" />
          <Badge
            variant="secondary"
            className="text-xs"
          >
            {hasModelData
              ? `${modelTokenData.length} model${modelTokenData.length !== 1 ? "s" : ""}`
              : `${tokenData.length} agent${tokenData.length !== 1 ? "s" : ""}`}
          </Badge>
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="w-96">
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <ChartNoAxesColumnIncreasing className="h-4 w-4" />
            <h4 className="text-sm font-semibold">Token Usage</h4>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <div className="flex items-center gap-1.5">
                <Zap className="h-3 w-3 text-blue-500 dark:text-blue-400" />
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
                <TrendingUp className="h-3 w-3 text-green-500 dark:text-green-400" />
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
                  <Coins className="h-3 w-3 text-amber-500 dark:text-amber-400" />
                  <span className="text-muted-foreground text-xs font-medium">
                    {hasModelData ? "Estimated Cost" : "Cost"}
                  </span>
                </div>
                <span className="text-sm font-semibold">
                  $
                  {(hasModelData ? totalModelCost : metrics.totalCost).toFixed(
                    2,
                  )}
                </span>
              </div>

              {metrics.totalSavings > 0 && (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-blue-600 dark:text-blue-400">
                      Cache Percentage
                    </span>
                    <Badge
                      variant="outline"
                      className="border-blue-200 text-blue-600 dark:border-blue-800 dark:text-blue-400"
                    >
                      {cachePercentage}%
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-green-600 dark:text-green-400">
                      Cache Savings
                    </span>
                    <Badge
                      variant="outline"
                      className="border-green-200 text-green-600 dark:border-green-800 dark:text-green-400"
                    >
                      -${metrics.totalSavings.toFixed(2)}
                    </Badge>
                  </div>
                </>
              )}
            </div>
          </div>

          {hasModelData && modelTokenData.length > 1 && (
            <>
              <Separator />
              <Collapsible
                open={isExpanded}
                onOpenChange={setIsExpanded}
              >
                <CollapsibleTrigger className="hover:text-foreground flex w-full items-center justify-between pb-2 text-sm font-medium">
                  <span>Per-Model Breakdown</span>
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                </CollapsibleTrigger>
                <CollapsibleContent className="scrollbar-pretty-auto max-h-80 space-y-3 pt-1 pr-2">
                  {modelTokenData.map((model, index) => {
                    const modelCost = calculateModelCost(model);
                    const modelTotalTokens =
                      model.inputTokens +
                      model.outputTokens +
                      model.cacheCreationInputTokens +
                      model.cacheReadInputTokens;
                    const modelCachedTokens =
                      model.cacheCreationInputTokens +
                      model.cacheReadInputTokens;
                    const modelCachePercentage =
                      modelTotalTokens > 0
                        ? (
                            (modelCachedTokens / modelTotalTokens) *
                            100
                          ).toFixed(1)
                        : "0";

                    return (
                      <div
                        key={index}
                        className="space-y-2 rounded-lg border p-3"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground truncate text-xs font-medium">
                            {model.model.replace(/^(anthropic|openai):/, "")}
                          </span>
                          <Badge
                            variant="outline"
                            className="text-xs"
                          >
                            ${modelCost.toFixed(3)}
                          </Badge>
                        </div>

                        <div className="space-y-2 text-xs">
                          <div className="flex justify-between">
                            <div className="flex items-center gap-1.5">
                              <Zap className="h-3 w-3 text-blue-500 dark:text-blue-400" />
                              <span className="text-muted-foreground font-medium">
                                Input
                              </span>
                            </div>
                            <span className="font-semibold">
                              {(
                                model.inputTokens +
                                model.cacheCreationInputTokens +
                                model.cacheReadInputTokens
                              ).toLocaleString()}
                            </span>
                          </div>
                          <div className="flex justify-between">
                            <div className="flex items-center gap-1.5">
                              <TrendingUp className="h-3 w-3 text-green-500 dark:text-green-400" />
                              <span className="text-muted-foreground font-medium">
                                Output
                              </span>
                            </div>
                            <span className="font-semibold">
                              {model.outputTokens.toLocaleString()}
                            </span>
                          </div>
                        </div>

                        {modelCachedTokens > 0 && (
                          <div className="flex justify-between text-xs">
                            <span className="text-xs font-medium text-blue-600 dark:text-blue-400">
                              Cache Percentage
                            </span>
                            <Badge
                              variant="outline"
                              className="border-blue-200 text-blue-600 dark:border-blue-800 dark:text-blue-400"
                            >
                              {modelCachePercentage}%
                            </Badge>
                          </div>
                        )}
                      </div>
                    );
                  })}

                  <div className="text-muted-foreground border-t pt-2 text-xs">
                    * Estimated costs. Please review all token usage and pricing
                    to ensure accuracy.
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
