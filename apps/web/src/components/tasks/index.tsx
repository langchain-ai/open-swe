"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
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
  Edit2,
  Save,
  Plus,
  Trash2,
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

interface TasksSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  taskPlan: TaskPlan;
  className?: string;
  onTaskChange?: (taskId: string) => void;
  onRevisionChange?: (taskId: string, revisionIndex: number) => void;
  onEditPlanItem?: (
    taskId: string,
    planItemIndex: number,
    newPlan: string,
  ) => void;
  onAddPlanItem?: (taskId: string, plan: string) => void;
  onDeletePlanItem?: (taskId: string, planItemIndex: number) => void;
}

interface TaskPlanViewProps {
  taskPlan: TaskPlan;
  onTaskChange?: (taskId: string) => void;
  onRevisionChange?: (taskId: string, revisionIndex: number) => void;
  onEditPlanItem?: (
    taskId: string,
    planItemIndex: number,
    newPlan: string,
  ) => void;
  onAddPlanItem?: (taskId: string, plan: string) => void;
  onDeletePlanItem?: (taskId: string, planItemIndex: number) => void;
}

type FilterType = "all" | "completed" | "current" | "pending";

// Tasks Sidebar Component
export function TasksSidebar({
  isOpen,
  onClose,
  taskPlan,
  onTaskChange,
  onRevisionChange,
  onEditPlanItem,
  onAddPlanItem,
  onDeletePlanItem,
}: TasksSidebarProps) {
  const [currentTaskIndex, setCurrentTaskIndex] = useState(
    taskPlan.activeTaskIndex,
  );
  const [currentRevisionIndex, setCurrentRevisionIndex] = useState(0);
  const [expandedSummaries, setExpandedSummaries] = useState<Set<number>>(
    new Set(),
  );
  const [editingPlanItem, setEditingPlanItem] = useState<number | null>(null);
  const [editingText, setEditingText] = useState("");
  const [newPlanItemText, setNewPlanItemText] = useState("");
  const [showAddPlanItem, setShowAddPlanItem] = useState(false);
  const [filter, setFilter] = useState<FilterType>("all");

  const currentTask = taskPlan.tasks[currentTaskIndex];
  const isLatestTask = currentTaskIndex === taskPlan.activeTaskIndex;
  const isLatestRevision =
    currentRevisionIndex === currentTask?.activeRevisionIndex;

  useEffect(() => {
    if (currentTask?.planRevisions) {
      setCurrentRevisionIndex(currentTask.activeRevisionIndex);
      setExpandedSummaries(new Set());
      setEditingPlanItem(null);
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

  const isPlanItemEditable = (item: PlanItem) => {
    return (
      isLatestTask &&
      isLatestRevision &&
      !item.completed &&
      item.index !== currentPlanItemIndex
    );
  };

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
        return <CheckCircle2 className="h-4 w-4 text-green-600" />;
      case "current":
        return <Play className="h-4 w-4 text-blue-500" />;
      default:
        return <Circle className="h-4 w-4 text-gray-400" />;
    }
  };

  const startEditing = (item: PlanItem) => {
    setEditingPlanItem(item.index);
    setEditingText(item.plan);
  };

  const saveEdit = () => {
    if (editingPlanItem !== null && editingText.trim()) {
      onEditPlanItem?.(currentTask.id, editingPlanItem, editingText.trim());
      setEditingPlanItem(null);
      setEditingText("");
    }
  };

  const addNewPlanItem = () => {
    if (newPlanItemText.trim()) {
      onAddPlanItem?.(currentTask.id, newPlanItemText.trim());
      setNewPlanItemText("");
      setShowAddPlanItem(false);
    }
  };

  const goToPreviousRevision = () => {
    if (currentRevisionIndex > 0) {
      const newIndex = currentRevisionIndex - 1;
      setCurrentRevisionIndex(newIndex);
      onRevisionChange?.(currentTask.id, newIndex);
      setExpandedSummaries(new Set());
      setEditingPlanItem(null);
    }
  };

  const goToNextRevision = () => {
    if (currentRevisionIndex < currentTask.planRevisions.length - 1) {
      const newIndex = currentRevisionIndex + 1;
      setCurrentRevisionIndex(newIndex);
      onRevisionChange?.(currentTask.id, newIndex);
      setExpandedSummaries(new Set());
      setEditingPlanItem(null);
    }
  };

  const goToLatestRevision = () => {
    const latestIndex = currentTask.activeRevisionIndex;
    setCurrentRevisionIndex(latestIndex);
    onRevisionChange?.(currentTask.id, latestIndex);
    setExpandedSummaries(new Set());
    setEditingPlanItem(null);
  };

  const formatDate = (timestamp: number) => {
    return new Date(timestamp).toLocaleString();
  };

  return (
    <div
      className={cn(
        "fixed top-0 right-0 z-10 h-screen border-l border-gray-200 bg-white shadow-lg transition-all duration-300",
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
        <div className="border-b border-gray-200 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Tasks</h2>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="h-8 w-8 p-0"
            >
              <PanelLeftClose className="h-4 w-4" />
            </Button>
          </div>

          {/* Task selector */}
          {taskPlan.tasks.length > 1 && (
            <div className="mb-3">
              <label className="mb-1 block text-xs font-medium text-gray-700">
                Task
              </label>
              <select
                className="w-full rounded border border-gray-200 px-2 py-1 text-sm"
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
            <label className="mb-1 block text-xs font-medium text-gray-700">
              Plan Revision
            </label>
            <div className="flex items-center gap-2">
              <div className="flex flex-1 items-center gap-1 rounded border border-gray-200 bg-gray-50 px-2 py-1">
                <div className="flex items-center gap-1">
                  {currentRevision?.createdBy === "agent" ? (
                    <Bot className="h-3 w-3 text-blue-500" />
                  ) : (
                    <User className="h-3 w-3 text-green-500" />
                  )}
                  <Clock className="h-3 w-3 text-gray-500" />
                </div>
                <span className="text-xs">
                  Rev {currentRevision?.revisionIndex + 1} of{" "}
                  {currentTask.planRevisions.length}
                </span>
                {!isLatestRevision && (
                  <span className="ml-1 rounded bg-orange-50 px-1 py-0.5 text-xs text-orange-600">
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
              <div className="mt-1 text-xs text-gray-500">
                Created by {currentRevision.createdBy} on{" "}
                {formatDate(currentRevision.createdAt)}
              </div>
            )}
          </div>

          {/* Filter controls */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 rounded border border-gray-200 bg-gray-50 px-2 py-1">
              <Filter className="h-3 w-3 text-gray-500" />
              <select
                className="border-none bg-transparent text-xs outline-none"
                value={filter}
                onChange={(e) => setFilter(e.target.value as FilterType)}
              >
                <option value="all">All</option>
                <option value="completed">Completed</option>
                <option value="current">Current</option>
                <option value="pending">Pending</option>
              </select>
            </div>

            {isLatestTask && isLatestRevision && (
              <span className="rounded bg-green-50 px-2 py-1 text-xs text-green-600">
                Editable
              </span>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="space-y-3">
            {filteredItems.length === 0 ? (
              <div className="py-8 text-center text-sm text-gray-500">
                No plan items match the current filter
              </div>
            ) : (
              filteredItems.map((item) => {
                const state = getItemState(item);
                const isExpanded = expandedSummaries.has(item.index);
                const isEditing = editingPlanItem === item.index;
                const editable = isPlanItemEditable(item);

                return (
                  <div
                    key={item.index}
                    className={cn(
                      "rounded-lg border p-3",
                      state === "current" && "border-blue-200 bg-blue-50",
                      state === "completed" && "border-green-200 bg-green-50",
                      state === "remaining" && "border-gray-200 bg-white",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex-shrink-0">
                        {getStateIcon(state)}
                      </div>

                      <div className="min-w-0 flex-1">
                        {isEditing ? (
                          <div className="space-y-2">
                            <Textarea
                              value={editingText}
                              onChange={(e) => setEditingText(e.target.value)}
                              className="min-h-[60px] text-sm"
                              placeholder="Enter plan item description..."
                            />
                            <div className="flex items-center gap-2">
                              <Button
                                size="sm"
                                onClick={saveEdit}
                                className="h-7 text-xs"
                              >
                                <Save className="mr-1 h-3 w-3" />
                                Save
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => {
                                  setEditingPlanItem(null);
                                  setEditingText("");
                                }}
                                className="h-7 text-xs"
                              >
                                Cancel
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="mb-1 flex items-start justify-between gap-2">
                              <p className="text-sm leading-relaxed text-gray-900">
                                {item.plan}
                              </p>
                              {editable && (
                                <div className="flex flex-shrink-0 items-center gap-1">
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => startEditing(item)}
                                    className="h-6 w-6 p-0"
                                  >
                                    <Edit2 className="h-3 w-3" />
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() =>
                                      onDeletePlanItem?.(
                                        currentTask.id,
                                        item.index,
                                      )
                                    }
                                    className="h-6 w-6 p-0 text-red-500"
                                  >
                                    <Trash2 className="h-3 w-3" />
                                  </Button>
                                </div>
                              )}
                            </div>

                            <div className="flex items-center justify-between">
                              <span className="text-xs text-gray-500">
                                Plan Item #{item.index + 1}
                              </span>
                              <span
                                className={cn(
                                  "rounded-full px-2 py-1 text-xs",
                                  state === "completed" &&
                                    "bg-green-100 text-green-700",
                                  state === "current" &&
                                    "bg-blue-100 text-blue-700",
                                  state === "remaining" &&
                                    "bg-gray-100 text-gray-700",
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
                                  const newExpanded = new Set(
                                    expandedSummaries,
                                  );
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
                                    className="h-6 p-0 text-xs text-green-700"
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
                                  <div className="rounded border border-green-200 bg-green-50 p-2 text-xs text-green-800">
                                    {item.summary}
                                  </div>
                                </CollapsibleContent>
                              </Collapsible>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })
            )}

            {/* Add New Plan Item - Only for latest task and revision */}
            {isLatestTask &&
              isLatestRevision &&
              !planItems.every((item) => item.completed) && (
                <div>
                  {showAddPlanItem ? (
                    <div className="rounded-lg border border-dashed border-gray-300 p-3">
                      <div className="space-y-2">
                        <Textarea
                          value={newPlanItemText}
                          onChange={(e) => setNewPlanItemText(e.target.value)}
                          placeholder="Enter new plan item description..."
                          className="min-h-[60px] text-sm"
                        />
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            onClick={addNewPlanItem}
                            disabled={!newPlanItemText.trim()}
                            className="h-7 text-xs"
                          >
                            <Plus className="mr-1 h-3 w-3" />
                            Add Plan Item
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setShowAddPlanItem(false);
                              setNewPlanItemText("");
                            }}
                            className="h-7 text-xs"
                          >
                            Cancel
                          </Button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <Button
                      variant="outline"
                      className="h-10 w-full border-dashed border-gray-300 text-sm"
                      onClick={() => setShowAddPlanItem(true)}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Add New Plan Item
                    </Button>
                  )}
                </div>
              )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Main TaskPlan View Component
export function TaskPlanView({
  taskPlan,
  onTaskChange,
  onRevisionChange,
  onEditPlanItem,
  onAddPlanItem,
  onDeletePlanItem,
}: TaskPlanViewProps) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  if (taskPlan.tasks.length === 0) {
    return (
      <div className="w-full rounded border border-gray-200 bg-gray-50 p-2">
        <div className="text-center text-xs text-gray-500">
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
        onRevisionChange={onRevisionChange}
        onEditPlanItem={onEditPlanItem}
        onAddPlanItem={onAddPlanItem}
        onDeletePlanItem={onDeletePlanItem}
      />
    </>
  );
}
