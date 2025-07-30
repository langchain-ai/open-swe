"use client";
import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  CheckCircle2,
  Circle,
  Play,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  Clock,
  Filter,
  PanelLeftClose,
  User,
  Bot,
  RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ProgressBar } from "./progress-bar";
import { PlanItem, TaskPlan } from "@open-swe/shared/open-swe/types";
import { BasicMarkdownText } from "../thread/markdown-text";

interface TasksSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  taskPlan: TaskPlan;
  className?: string;
  onTaskChange?: (taskId: string) => void;
}

interface TaskPlanViewProps {
  taskPlan: TaskPlan;
  onTaskChange?: (taskId: string) => void;
}

type FilterType = "all" | "completed" | "current" | "pending";

// Tasks Sidebar Component
export function TasksSidebar({
  isOpen,
  onClose,
  taskPlan,
  onTaskChange,
}: TasksSidebarProps) {
  const [currentTaskIndex, setCurrentTaskIndex] = useState(
    taskPlan.activeTaskIndex,
  );
  const [currentRevisionIndex, setCurrentRevisionIndex] = useState(0);
  const [expandedSummaries, setExpandedSummaries] = useState<Set<number>>(
    new Set(),
  );
  const [filter, setFilter] = useState<FilterType>("all");

  const currentTask = taskPlan.tasks[currentTaskIndex];
  const isLatestTask = currentTaskIndex === taskPlan.activeTaskIndex;
  const isLatestRevision =
    currentRevisionIndex === currentTask?.activeRevisionIndex;

  useEffect(() => {
    if (currentTask?.planRevisions) {
      setCurrentRevisionIndex(currentTask.activeRevisionIndex);
      setExpandedSummaries(new Set());
    }
  }, [currentTask]);

  if (!currentTask) return null;

  const currentRevision = currentTask.planRevisions[currentRevisionIndex];
  const planItems = currentRevision?.plans || [];
  const sortedItems = [...planItems].sort((a, b) => a.index - b.index);

  const currentPlanItemIndex = sortedItems
    .filter((item) => !item.completed)
    .reduce(
      (min, item) => (item.index < min ? item.index : min),
      Number.POSITIVE_INFINITY,
    );

  const filteredItems = sortedItems.filter((item) => {
    if (filter === "all") return true;
    if (filter === "completed") return item.completed;
    if (filter === "current") return item.index === currentPlanItemIndex;
    if (filter === "pending")
      return !item.completed && item.index !== currentPlanItemIndex;
    return true;
  });

  const getItemState = (
    item: PlanItem,
  ): "completed" | "current" | "remaining" => {
    if (item.completed) return "completed";
    if (item.index === currentPlanItemIndex) return "current";
    return "remaining";
  };

  const getStateIcon = (state: string) => {
    switch (state) {
      case "completed":
        return (
          <CheckCircle2 className="h-4 w-4 text-green-500 dark:text-green-400/80" />
        );
      case "current":
        return (
          <Play className="h-4 w-4 text-orange-600 dark:text-orange-400" />
        );
      default:
        return <Circle className="h-4 w-4 text-gray-400 dark:text-gray-500" />;
    }
  };

  const goToPreviousRevision = () => {
    if (currentRevisionIndex > 0) {
      const newIndex = currentRevisionIndex - 1;
      setCurrentRevisionIndex(newIndex);
      setExpandedSummaries(new Set());
    }
  };

  const goToNextRevision = () => {
    if (currentRevisionIndex < currentTask.planRevisions.length - 1) {
      const newIndex = currentRevisionIndex + 1;
      setCurrentRevisionIndex(newIndex);
      setExpandedSummaries(new Set());
    }
  };

  const goToLatestRevision = () => {
    const latestIndex = currentTask.activeRevisionIndex;
    setCurrentRevisionIndex(latestIndex);
    setExpandedSummaries(new Set());
  };

  const formatDate = (timestamp: number) => {
    return new Date(timestamp).toLocaleString();
  };

  return (
    <div
      className={cn(
        "dark:bg-background fixed top-0 right-0 z-10 h-screen bg-white shadow-lg transition-all duration-300",
        isOpen ? "w-80 md:w-xl" : "w-0 overflow-hidden border-l-0",
      )}
    >
      <div
        className={cn(
          "flex h-full flex-col transition-opacity duration-300",
          isOpen ? "opacity-100 delay-150" : "opacity-0",
        )}
        style={{ minWidth: isOpen ? "320px" : "0" }}
      >
        {/* Header */}
        <div className="border-b border-gray-200 p-4 dark:border-gray-700">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Tasks
            </h2>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="h-8 w-8 p-0 hover:bg-gray-100 dark:hover:bg-gray-700/50"
            >
              <PanelLeftClose className="h-4 w-4" />
            </Button>
          </div>

          {/* Task selector */}
          {taskPlan.tasks.length > 1 && (
            <div className="mb-3">
              <label className="mb-1 block text-xs font-medium text-gray-700 dark:text-gray-300">
                Task
              </label>
              <select
                className="bg-muted dark:bg-muted w-full rounded border border-gray-200 px-2 py-1 text-sm dark:border-gray-600 dark:text-gray-200"
                value={currentTask.id}
                onChange={(e) => {
                  const newTaskIndex = taskPlan.tasks.findIndex(
                    (t) => t.id === e.target.value,
                  );
                  if (newTaskIndex !== -1) {
                    setCurrentTaskIndex(newTaskIndex);
                    onTaskChange?.(e.target.value);
                  }
                }}
              >
                {taskPlan.tasks.map((task, index) => (
                  <option
                    key={task.id}
                    value={task.id}
                  >
                    Task #{task.taskIndex + 1}: {task.request.substring(0, 50)}
                    {index === taskPlan.activeTaskIndex && " (Active)"}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Plan revision navigation */}
          <div className="mb-3">
            <label className="mb-1 block text-xs font-medium text-gray-700 dark:text-gray-300">
              Plan Revision
            </label>
            <div className="flex items-center gap-2">
              <div className="bg-muted dark:bg-muted flex flex-1 items-center gap-1 rounded border border-gray-200 px-2 py-1 dark:border-gray-600">
                <div className="flex items-center gap-1">
                  {currentRevision?.createdBy === "agent" ? (
                    <Bot className="h-3 w-3 text-gray-600 dark:text-gray-400" />
                  ) : (
                    <User className="h-3 w-3 text-green-500 dark:text-green-400/80" />
                  )}
                  <Clock className="h-3 w-3 text-gray-500 dark:text-gray-400" />
                </div>
                <span className="text-xs dark:text-gray-300">
                  Rev {currentRevision?.revisionIndex + 1} of{" "}
                  {currentTask.planRevisions.length}
                </span>
                {!isLatestRevision && (
                  <span className="ml-1 rounded bg-orange-50 px-1 py-0.5 text-xs text-orange-600 dark:bg-orange-900/30 dark:text-orange-400">
                    Historical
                  </span>
                )}
              </div>

              {currentTask.planRevisions.length > 1 && (
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={goToPreviousRevision}
                    disabled={currentRevisionIndex === 0}
                    className="h-6 w-6 p-0"
                    title="Previous revision"
                  >
                    <ChevronLeft className="h-3 w-3" />
                  </Button>

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={goToNextRevision}
                    disabled={
                      currentRevisionIndex ===
                      currentTask.planRevisions.length - 1
                    }
                    className="h-6 w-6 p-0"
                    title="Next revision"
                  >
                    <ChevronRight className="h-3 w-3" />
                  </Button>

                  {!isLatestRevision && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={goToLatestRevision}
                      className="h-6 w-6 p-0"
                      title="Latest revision"
                    >
                      <RotateCcw className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              )}
            </div>

            {/* Revision info */}
            {currentRevision && (
              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Created by {currentRevision.createdBy} on{" "}
                {formatDate(currentRevision.createdAt)}
              </div>
            )}
          </div>

          {/* Filter controls */}
          <div className="flex items-center gap-2">
            <div className="bg-muted dark:bg-muted flex items-center gap-1 rounded border border-gray-200 px-2 py-1 dark:border-gray-600">
              <Filter className="h-3 w-3 text-gray-500 dark:text-gray-400" />
              <select
                className="border-none bg-transparent text-xs outline-none dark:text-gray-300"
                value={filter}
                onChange={(e) => setFilter(e.target.value as FilterType)}
              >
                <option value="all">All</option>
                <option value="completed">Completed</option>
                <option value="current">Current</option>
                <option value="pending">Pending</option>
              </select>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="space-y-3">
            {filteredItems.length === 0 ? (
              <div className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                No plan items match the current filter
              </div>
            ) : (
              filteredItems.map((item) => {
                const state = getItemState(item);
                const isExpanded = expandedSummaries.has(item.index);

                return (
                  <div
                    key={item.index}
                    className={cn(
                      "rounded-lg border p-3",
                      state === "current" &&
                        "border-orange-200 bg-orange-50 dark:border-orange-800/50 dark:bg-orange-950/20",
                      state === "completed" &&
                        "border-green-200/80 bg-green-50/60 dark:border-green-800/30 dark:bg-green-950/15",
                      state === "remaining" &&
                        "bg-muted dark:bg-muted border-gray-200 dark:border-gray-700",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex-shrink-0">
                        {getStateIcon(state)}
                      </div>

                      <div className="min-w-0 flex-1">
                        <>
                          <div className="mb-1 flex items-start justify-between gap-2">
                            <BasicMarkdownText className="text-sm leading-relaxed text-gray-900 dark:text-gray-100">
                              {item.plan}
                            </BasicMarkdownText>
                          </div>

                          <div className="flex items-center justify-between">
                            <span className="text-xs text-gray-500 dark:text-gray-400">
                              Plan Item #{item.index + 1}
                            </span>
                            <span
                              className={cn(
                                "rounded-full px-2 py-1 text-xs",
                                state === "completed" &&
                                  "bg-green-100/70 text-green-600 dark:bg-green-900/20 dark:text-green-300/90",
                                state === "current" &&
                                  "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
                                state === "remaining" &&
                                  "bg-muted dark:bg-muted text-gray-700 dark:text-gray-300",
                              )}
                            >
                              {state === "completed"
                                ? "Completed"
                                : state === "current"
                                  ? "In Progress"
                                  : "Pending"}
                            </span>
                          </div>

                          {item.completed && item.summary && (
                            <Collapsible
                              open={isExpanded}
                              onOpenChange={() => {
                                const newExpanded = new Set(expandedSummaries);
                                if (newExpanded.has(item.index)) {
                                  newExpanded.delete(item.index);
                                } else {
                                  newExpanded.add(item.index);
                                }
                                setExpandedSummaries(newExpanded);
                              }}
                              className="mt-2"
                            >
                              <CollapsibleTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 p-0 text-xs text-green-600 dark:text-green-300/90"
                                >
                                  {isExpanded ? (
                                    <ChevronDown className="mr-1 h-3 w-3" />
                                  ) : (
                                    <ChevronRight className="mr-1 h-3 w-3" />
                                  )}
                                  View summary
                                </Button>
                              </CollapsibleTrigger>
                              <CollapsibleContent className="mt-2">
                                <div className="rounded border border-green-200/80 bg-green-50/60 p-2 text-xs text-green-700 dark:border-green-800/30 dark:bg-green-950/15 dark:text-green-300/90">
                                  <BasicMarkdownText>
                                    {item.summary}
                                  </BasicMarkdownText>
                                </div>
                              </CollapsibleContent>
                            </Collapsible>
                          )}
                        </>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Main TaskPlan View Component
export function TaskPlanView({ taskPlan, onTaskChange }: TaskPlanViewProps) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  if (taskPlan.tasks.length === 0) {
    return (
      <div className="bg-muted dark:bg-muted w-full rounded border border-gray-200 p-2 dark:border-gray-700">
        <div className="text-center text-xs text-gray-500 dark:text-gray-400">
          No tasks available
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Compact Progress Bar */}
      <ProgressBar
        taskPlan={taskPlan}
        onOpenSidebar={() => setIsSidebarOpen(true)}
      />

      {/* Tasks Sidebar */}
      <TasksSidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        taskPlan={taskPlan}
        onTaskChange={onTaskChange}
      />
    </>
  );
}
