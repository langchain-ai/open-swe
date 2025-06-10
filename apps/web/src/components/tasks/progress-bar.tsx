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
import { PlanItem, TaskPlan } from "@open-swe/shared/open-swe/types";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

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
          "mt-2 w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 sm:mt-4",
          className,
        )}
      >
        <div className="flex items-center justify-between">
          <div className="text-xs text-gray-500">No active plan</div>
        </div>
      </div>
    );
  }

  const currentTask = taskPlan.tasks[taskPlan.activeTaskIndex];
  if (!currentTask || !currentTask.planRevisions.length) {
    return (
      <div
        className={cn(
          "mt-2 w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 sm:mt-4",
          className,
        )}
      >
        <div className="flex items-center justify-between">
          <div className="text-xs text-gray-500">No active plan</div>
        </div>
      </div>
    );
  }

  const activeRevision =
    currentTask.planRevisions[currentTask.activeRevisionIndex];
  const planItems = [...activeRevision.plans].sort((a, b) => a.index - b.index);

  // Find the current task (lowest index among uncompleted tasks)
  const currentTaskIndex = planItems
    .filter((item) => !item.completed)
    .reduce(
      (min, item) => (item.index < min ? item.index : min),
      Number.POSITIVE_INFINITY,
    );

  const getItemState = (
    item: PlanItem,
  ): "completed" | "current" | "remaining" => {
    if (item.completed) return "completed";
    if (item.index === currentTaskIndex) return "current";
    return "remaining";
  };

  const completedCount = planItems.filter((item) => item.completed).length;
  const progressPercentage =
    planItems.length > 0 ? (completedCount / planItems.length) * 100 : 0;

  const getSegmentColor = (state: string) => {
    switch (state) {
      case "completed":
        return "bg-green-400";
      case "current":
        return "bg-blue-400";
      default:
        return "bg-gray-200";
    }
  };

  return (
    <div
      className={cn(
        "w-full rounded-md border border-gray-200 bg-white shadow-sm",
        className,
      )}
    >
      {/* Compact header */}
      <div className="overflow-hidden px-1 py-1.5 sm:px-2">
        <div className="mb-1 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-gray-700 sm:text-sm">
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
                    <HelpCircle className="h-3 w-3 text-gray-500" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent
                  side="top"
                  className="max-w-xs p-2 text-xs"
                >
                  <div className="space-y-1">
                    <p className="font-medium">Plan Progress</p>
                    <p>
                      Shows the current progress of the AI agent's plan
                      execution.
                    </p>
                  </div>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500 sm:text-sm">
              {Math.round(progressPercentage)}%
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={onOpenSidebar}
              className="h-6 border-blue-200 text-xs hover:bg-blue-50"
            >
              <List className="size-3" />
              <span className="hidden sm:inline">Tasks</span>
              <span className="sm:hidden">View</span>
            </Button>
          </div>
        </div>

        {/* Legend - conditionally shown */}
        {showLegend && (
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs sm:gap-3">
            <div className="flex items-center gap-1">
              <div className="h-2 w-2 rounded-full bg-green-400"></div>
              <span>Completed</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="h-2 w-2 animate-pulse rounded-full bg-blue-400"></div>
              <span>Current</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="h-2 w-2 rounded-full bg-gray-200"></div>
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

        {/* Progress Stats */}
        <div className="mb-2 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-xs text-gray-600 sm:text-sm">
            {completedCount} of {planItems.length} tasks completed
          </span>
          <span className="text-xs text-gray-500 sm:text-sm">
            Task #{currentTask.taskIndex + 1}
          </span>
        </div>

        {/* Progress Bar */}
        <div className="space-y-2">
          <div
            className="flex h-3 cursor-pointer touch-manipulation gap-[1px] overflow-hidden rounded-sm bg-gray-100 transition-all sm:h-2"
            onClick={onOpenSidebar}
            aria-label="Click to view all tasks"
            title="Click to view all tasks"
          >
            {planItems.map((item) => {
              const state = getItemState(item);
              const segmentWidth = `${100 / planItems.length}%`;

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
                        <span className="text-xs text-gray-500">
                          {state === "completed"
                            ? "Completed"
                            : state === "current"
                              ? "Current"
                              : "Pending"}
                        </span>
                      </div>
                      <p className="text-xs break-words">{item.plan}</p>
                    </div>
                  </HoverCardContent>
                </HoverCard>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
