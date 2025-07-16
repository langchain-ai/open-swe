"use client";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { List } from "lucide-react";
import { cn } from "@/lib/utils";
import { PlanItem, TaskPlan } from "@open-swe/shared/open-swe/types";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { memo } from "react";

interface ProgressBarProps {
  taskPlan?: TaskPlan;
  className?: string;
  onOpenSidebar?: () => void;
}

// Custom comparison function for memo to prevent unnecessary re-renders
function areProgressBarPropsEqual(
  prevProps: ProgressBarProps,
  nextProps: ProgressBarProps,
): boolean {
  // Compare non-task plan props
  if (prevProps.className !== nextProps.className) return false;
  if (prevProps.onOpenSidebar !== nextProps.onOpenSidebar) return false;

  // Handle task plan comparison
  const prevTaskPlan = prevProps.taskPlan;
  const nextTaskPlan = nextProps.taskPlan;

  // If both are undefined/null, they're equal
  if (!prevTaskPlan && !nextTaskPlan) return true;
  
  // If one is undefined and the other isn't, they're not equal
  if (!prevTaskPlan || !nextTaskPlan) return false;

  // Compare key task plan properties that affect the progress bar display
  if (prevTaskPlan.activeTaskIndex !== nextTaskPlan.activeTaskIndex) return false;
  if (prevTaskPlan.tasks.length !== nextTaskPlan.tasks.length) return false;

  // Compare active task details
  const prevActiveTask = prevTaskPlan.tasks[prevTaskPlan.activeTaskIndex];
  const nextActiveTask = nextTaskPlan.tasks[nextTaskPlan.activeTaskIndex];

  if (!prevActiveTask || !nextActiveTask) return false;
  if (prevActiveTask.activeRevisionIndex !== nextActiveTask.activeRevisionIndex) return false;

  // Compare plan items completion status in the active revision
  const prevActiveRevision = prevActiveTask.planRevisions[prevActiveTask.activeRevisionIndex];
  const nextActiveRevision = nextActiveTask.planRevisions[nextActiveTask.activeRevisionIndex];

  if (!prevActiveRevision || !nextActiveRevision) return false;
  if (prevActiveRevision.plans.length !== nextActiveRevision.plans.length) return false;

  // Check if any plan item completion status changed
  for (let i = 0; i < prevActiveRevision.plans.length; i++) {
    if (prevActiveRevision.plans[i].completed !== nextActiveRevision.plans[i].completed) {
      return false;
    }
  }

  return true;
}

export const ProgressBar = memo(function ProgressBar({
  taskPlan,
  className,
  onOpenSidebar,
}: ProgressBarProps) {
  // Early return for no task plan
  if (!taskPlan || !taskPlan.tasks.length) {
    return (
      <div
        className={cn(
          "mt-2 w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 sm:mt-4 dark:border-gray-700 dark:bg-gray-800",
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

  const currentTask = taskPlan.tasks[taskPlan.activeTaskIndex];
  if (!currentTask || !currentTask.planRevisions.length) {
    return (
      <div
        className={cn(
          "mt-2 w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 sm:mt-4 dark:border-gray-700 dark:bg-gray-800",
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

  const currentRevision =
    currentTask.planRevisions[currentTask.activeRevisionIndex];
  const planItems = currentRevision?.plans || [];

  const completedCount = planItems.filter((item) => item.completed).length;
  const progressPercentage =
    planItems.length > 0 ? (completedCount / planItems.length) * 100 : 0;

  // Find the current task (lowest index among uncompleted tasks)
  const currentTaskIndex = planItems
    .filter((item) => !item.completed)
    .reduce(
      (min, item) => (item.index < min ? item.index : min),
      Number.POSITIVE_INFINITY,
    );

  const getItemState = (item: PlanItem) => {
    if (item.completed) return "completed";
    if (item.index === currentTaskIndex) return "current";
    return "remaining";
  };

  // Create a unique key based on completion state to force re-render when tasks complete
  const progressKey = `${taskPlan.activeTaskIndex}-${currentTask.activeRevisionIndex}-${completedCount}-${planItems.length}`;

  return (
    <div
      className={cn(
        "w-96 rounded-md border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900",
        className,
      )}
      key={progressKey}
    >
      {/* Compact header - 2 rows instead of 3 */}
      <div className="overflow-hidden px-2 py-1">
        {/* Row 1: Title, progress stats, and Tasks button */}
        <div className="mb-1 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-gray-700 dark:text-gray-200">
              Plan Progress
            </span>
            <span className="text-xs text-gray-600 dark:text-gray-300">
              {completedCount} of {planItems.length} completed
            </span>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {Math.round(progressPercentage)}%
            </span>
          </div>
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

        {/* Row 2: Progress Bar */}
        <div>
          <div
            className="flex h-4 cursor-pointer touch-manipulation gap-[1px] overflow-hidden rounded-sm bg-gray-100 transition-all dark:bg-gray-700"
            onClick={onOpenSidebar}
            aria-label="Click to view all tasks"
            title="Click to view all tasks"
          >
            {planItems.map((item) => {
              const state = getItemState(item);
              const segmentWidth = `${100 / planItems.length}%`;

              return (
                <HoverCard key={`${progressKey}-item-${item.index}`}>
                  <HoverCardTrigger asChild>
                    <div
                      className={cn(
                        "transition-all duration-200 relative",
                        state === "completed" && "bg-green-400",
                        state === "current" && "bg-blue-400 ",
                        state === "remaining" && "bg-gray-200",
                      )}
                      style={{ width: segmentWidth }}
                      role="button"
                      tabIndex={0}
                    />
                  </HoverCardTrigger>
                  <HoverCardContent
                    side="top"
                    className="z-50 w-64 p-2 text-xs"
                  >
                    <div className="space-y-1">
                      <p className="font-medium">Item #{item.index + 1}</p>
                      <p className="text-muted-foreground">{item.plan}</p>
                      <p
                        className={cn(
                          "text-xs",
                          state === "completed" && "text-green-600",
                          state === "current" && "text-blue-600",
                          state === "remaining" && "text-gray-500",
                        )}
                      >
                        Status: {state === "completed" ? "Completed" : state === "current" ? "In Progress" : "Pending"}
                      </p>
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
}, areProgressBarPropsEqual);
