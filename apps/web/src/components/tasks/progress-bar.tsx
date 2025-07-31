"use client";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { List, HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { PlanItem, Task, TaskPlan } from "@open-swe/shared/open-swe/types";
import {
  getActivePlanItems,
  getActiveTask,
} from "@open-swe/shared/open-swe/tasks";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { InlineMarkdownText } from "@/components/thread/markdown-text";

interface ProgressBarProps {
  taskPlan?: TaskPlan;
  className?: string;
  onOpenSidebar?: () => void;
}

export function ProgressBar({
  taskPlan,
  className,
  onOpenSidebar,
}: ProgressBarProps) {
  const [showLegend, setShowLegend] = useState(false);

  if (!taskPlan || !taskPlan.tasks.length) {
    return (
      <div
        className={cn(
          "w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 sm:mt-4 dark:border-gray-600/50 dark:bg-gray-700/30",
          className,
        )}
      >
        <div className="flex items-center justify-between">
          <div className="text-xs text-gray-500 dark:text-gray-400">
            No active plan
          </div>
        </div>
      </div>
    );
  }

  let currentTask: Task | null = null;
  let planItems: PlanItem[] = [];
  let sortedPlanItems: PlanItem[] = [];
  let currentTaskIndex: number | null = null;

  try {
    currentTask = getActiveTask(taskPlan);
    planItems = getActivePlanItems(taskPlan);
    sortedPlanItems = [...planItems].sort((a, b) => a.index - b.index);

    // Find the current task (lowest index among uncompleted tasks)
    currentTaskIndex = sortedPlanItems
      .filter((item) => !item.completed)
      .reduce(
        (min, item) => (item.index < min ? item.index : min),
        Number.POSITIVE_INFINITY,
      );
  } catch (error) {
    return (
      <div
        className={cn(
          "w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 sm:mt-4 dark:border-gray-600/50 dark:bg-gray-700/30",
          className,
        )}
      >
        <div className="flex items-center justify-between">
          <div className="text-xs text-gray-500 dark:text-gray-400">
            No active plan
          </div>
        </div>
      </div>
    );
  }

  const getItemState = (
    item: PlanItem,
  ): "completed" | "current" | "remaining" => {
    if (item.completed) return "completed";
    if (item.index === currentTaskIndex) return "current";
    return "remaining";
  };

  const completedCount = sortedPlanItems.filter(
    (item) => item.completed,
  ).length;
  const progressPercentage =
    sortedPlanItems.length > 0
      ? (completedCount / sortedPlanItems.length) * 100
      : 0;

  const getSegmentColor = (state: string) => {
    switch (state) {
      case "completed":
        return "bg-green-500/90 dark:bg-green-400/80";
      case "current":
        return "bg-blue-400 dark:bg-blue-500";
      default:
        return "bg-gray-200 dark:bg-gray-600";
    }
  };

  return (
    <div
      className={cn(
        "dark:bg-muted w-full overflow-hidden rounded-lg bg-gray-50 px-1 py-1.5 sm:px-3",
        className,
      )}
    >
      {/* Progress Stats */}
      <div className="mb-2 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-700 sm:text-sm dark:text-gray-200">
            Plan Progress
          </span>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-4 w-4 p-0 sm:h-5 sm:w-5"
                  onClick={() => setShowLegend(!showLegend)}
                >
                  <HelpCircle className="h-3 w-3 text-gray-500 dark:text-gray-400" />
                </Button>
              </TooltipTrigger>
              <TooltipContent
                side="top"
                className="bg-popover text-popover-foreground max-w-xs border p-2 text-xs before:hidden after:hidden"
                sideOffset={5}
              >
                <div className="space-y-1">
                  <p className="text-foreground font-medium">Plan Progress</p>
                  <p className="text-muted-foreground">
                    Shows the current progress of the AI agent's plan execution.
                  </p>
                </div>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <span className="text-xs text-gray-600 sm:text-sm dark:text-gray-300">
            {completedCount} of {sortedPlanItems.length} tasks completed
          </span>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500 sm:text-sm dark:text-gray-400">
              {Math.round(progressPercentage)}%
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={onOpenSidebar}
              className="hover:bg-muted/80 hover:border-muted-foreground/50 h-6 text-xs"
            >
              <List className="size-3" />
              <span className="hidden sm:inline">Tasks</span>
              <span className="sm:hidden">View</span>
            </Button>
          </div>
        </div>
      </div>

      {showLegend && (
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs sm:gap-3 dark:border-gray-600/50 dark:bg-gray-700/30 dark:text-gray-200">
          <div className="flex items-center gap-1">
            <div className="h-2 w-2 rounded-full bg-green-500/90 dark:bg-green-400/80"></div>
            <span>Completed</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="h-2 w-2 animate-pulse rounded-full bg-blue-400 dark:bg-blue-500"></div>
            <span>Current</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="h-2 w-2 rounded-full bg-gray-200 dark:bg-gray-600"></div>
            <span>Pending</span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-4 touch-manipulation p-0 text-xs"
            onClick={() => setShowLegend(false)}
          >
            ×
          </Button>
        </div>
      )}

      {/* Progress Bar */}
      <div className="space-y-2">
        <div
          className="flex h-3 cursor-pointer touch-manipulation gap-[1px] overflow-hidden rounded-sm bg-gray-100 transition-all sm:h-2 dark:bg-gray-700"
          onClick={onOpenSidebar}
          aria-label="Click to view all tasks"
          title="Click to view all tasks"
        >
          {sortedPlanItems.map((item) => {
            const state = getItemState(item);
            const segmentWidth = `${100 / sortedPlanItems.length}%`;

            return (
              <HoverCard key={item.index}>
                <HoverCardTrigger asChild>
                  <div
                    className={cn(
                      "transition-all hover:opacity-80",
                      getSegmentColor(state),
                      state === "current" && "animate-pulse",
                    )}
                    style={{ width: segmentWidth }}
                  />
                </HoverCardTrigger>
                <HoverCardContent
                  side="bottom"
                  className="min-w-xs p-2 text-xs sm:max-w-sm md:max-w-lg"
                >
                  <div className="space-y-1">
                    <div className="flex flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-1">
                      <span className="font-medium">
                        Task #{item.index + 1}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {state === "completed"
                          ? "Completed"
                          : state === "current"
                            ? "Current"
                            : "Pending"}
                      </span>
                    </div>
                    <InlineMarkdownText className="text-xs break-words">
                      {item.plan}
                    </InlineMarkdownText>
                  </div>
                </HoverCardContent>
              </HoverCard>
            );
          })}
        </div>
      </div>
    </div>
  );
}
