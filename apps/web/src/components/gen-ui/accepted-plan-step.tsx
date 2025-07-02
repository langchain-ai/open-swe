"use client";

import {
  CheckCircle,
  ChevronDown,
  Sparkles,
  Circle,
  Check,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";

type PlanItem = {
  index: number;
  plan: string;
  completed: boolean;
};

type AcceptedPlanStepProps = {
  planTitle?: string;
  planItems?: PlanItem[];
  interruptType?: "accept" | "edit";
  collapse?: boolean;
};

export function AcceptedPlanStep({
  planTitle,
  planItems = [],
  interruptType,
  collapse: collapseProp = true,
}: AcceptedPlanStepProps) {
  const [collapsed, setCollapsed] = useState(collapseProp);

  const getStatusText = () => {
    if (interruptType === "edit") {
      return "Plan revised and approved";
    }
    return "Plan approved and ready";
  };

  const getStatusBadge = () => {
    return (
      <Badge
        variant="secondary"
        className="border-emerald-200 bg-emerald-100 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
      >
        <Check className="h-3 w-3" />
        Approved
      </Badge>
    );
  };

  const getPlanItemIcon = (item: PlanItem) => {
    if (item.completed) {
      return (
        <div className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-950">
          <CheckCircle className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
        </div>
      );
    }
    return (
      <div className="bg-muted flex h-6 w-6 items-center justify-center rounded-full">
        <Circle className="text-muted-foreground h-3.5 w-3.5" />
      </div>
    );
  };

  const completedCount = planItems.filter((item) => item.completed).length;
  const totalCount = planItems.length;

  return (
    <div className="group via-background to-background dark:via-background dark:to-background overflow-hidden rounded-xl border bg-gradient-to-br from-emerald-50/50 shadow-sm transition-shadow hover:shadow-md dark:from-emerald-950/20">
      {/* Header */}
      <div className="relative flex items-center border-b bg-gradient-to-r from-emerald-50 to-emerald-50/50 p-4 backdrop-blur-sm dark:from-emerald-950/30 dark:to-emerald-950/10">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500 shadow-md dark:bg-emerald-600">
          <Sparkles className="h-4 w-4 text-white" />
        </div>

        <div className="ml-3 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-foreground text-sm font-semibold">
              {getStatusText()}
            </h3>
            {getStatusBadge()}
          </div>
          {totalCount > 0 && (
            <p className="text-muted-foreground mt-1 text-xs">
              {completedCount} of {totalCount} steps completed
            </p>
          )}
        </div>

        <Button
          aria-label={
            collapsed ? "Expand plan details" : "Collapse plan details"
          }
          onClick={() => setCollapsed((c) => !c)}
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 hover:bg-emerald-100 dark:hover:bg-emerald-950/50"
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 transition-transform duration-200",
              collapsed ? "rotate-0" : "rotate-180",
            )}
          />
        </Button>
      </div>

      {/* Content */}
      <div
        className={cn(
          "overflow-hidden transition-all duration-300 ease-in-out",
          collapsed ? "max-h-0" : "max-h-[500px]",
        )}
      >
        <div className="space-y-4 p-4">
          {planTitle && (
            <div className="bg-card/50 rounded-lg border p-3">
              <h4 className="text-foreground mb-1 text-sm font-medium">
                Plan Overview
              </h4>
              <p className="text-muted-foreground text-sm leading-relaxed">
                {planTitle}
              </p>
            </div>
          )}

          {planItems.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-foreground flex items-center gap-2 text-sm font-medium">
                Execution Steps
                <Badge
                  variant="outline"
                  className="text-xs"
                >
                  {totalCount}
                </Badge>
              </h4>

              <div className="space-y-3">
                {planItems
                  .sort((a, b) => a.index - b.index)
                  .map((item, idx) => (
                    <div
                      key={item.index}
                      className={cn(
                        "group/item flex items-start gap-3 rounded-lg p-3 transition-colors duration-200",
                        item.completed
                          ? "bg-emerald-50/50 dark:bg-emerald-950/20"
                          : "bg-muted/30 hover:bg-muted/50",
                      )}
                    >
                      <div className="mt-0.5 flex-shrink-0">
                        {getPlanItemIcon(item)}
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <p
                            className={cn(
                              "text-sm leading-relaxed",
                              item.completed
                                ? "text-foreground"
                                : "text-muted-foreground",
                            )}
                          >
                            {item.plan}
                          </p>
                          <Badge
                            variant="outline"
                            className={cn(
                              "flex-shrink-0 text-xs",
                              item.completed &&
                                "border-emerald-200 text-emerald-700 dark:border-emerald-800 dark:text-emerald-400",
                            )}
                          >
                            {idx + 1}
                          </Badge>
                        </div>
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
