"use client";
import { memo } from "react";
import { differenceInHours, differenceInMinutes, format } from "date-fns";
import { GitBranch, ArrowRight, ListTodo } from "lucide-react";
import { useThreadsContext } from "@/providers/Thread";
import { cn } from "@/lib/utils";
import { StatusIndicator } from "@/components/status-indicator";
import { GitHubSVG } from "./icons/github";
import { useQueryState } from "nuqs";
import { Thread } from "@langchain/langgraph-sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getThreadTasks, getThreadTitle } from "@/lib/thread";

interface ThreadItemProps {
  thread: Thread<GraphState>;
  onClick: (thread: Thread<GraphState>) => void;
  variant?: "sidebar" | "dashboard";
  className?: string;
}

function formatRelativeDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();

  const minutesAgo = differenceInMinutes(now, date);
  const hoursAgo = differenceInHours(now, date);

  // Within the last hour - show minutes ago
  if (minutesAgo < 60) {
    return `${minutesAgo} min ago`;
  }

  // Between 1-24 hours ago - show hours ago
  if (hoursAgo < 24) {
    return `${hoursAgo} hour${hoursAgo === 1 ? "" : "s"} ago`;
  }

  // More than 24 hours ago - show month, day format
  return format(date, "MMM do");
}

export const ThreadItem = memo(function ThreadItem({
  thread,
  onClick,
  variant = "dashboard",
  className,
}: ThreadItemProps) {
  const [threadId] = useQueryState("threadId");
  const { recentlyUpdatedThreads } = useThreadsContext();
  const isSelected = thread.thread_id === threadId;
  const isSidebar = variant === "sidebar";
  const isRecentlyUpdated = recentlyUpdatedThreads.has(thread.thread_id);

  const displayDate = formatRelativeDate(thread.created_at);

  const { totalTasks, completedTasks } = getThreadTasks(thread);

  return (
    <div
      className={cn(
        "group cursor-pointer rounded-md border border-gray-200 bg-inherit p-2 shadow-sm transition-colors hover:bg-gray-50 hover:shadow-md",
        isSelected && "border-primary",
        isRecentlyUpdated && "animate-pulse border-blue-200 bg-blue-50",
        className,
      )}
      onClick={() => {
        if (!isSelected) {
          onClick(thread);
        }
      }}
    >
      <div className="flex items-start gap-1.5">
        <div className="flex w-full min-w-0 flex-col gap-1">
          <div className="flex w-full items-center gap-1.5">
            <StatusIndicator status={thread.status} />
            <h4 className="w-full truncate text-xs leading-tight font-medium text-gray-900">
              {getThreadTitle(thread)}
            </h4>
          </div>

          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-gray-500">
            <div className="mr-1 flex flex-row items-center justify-start gap-1">
              <GitHubSVG
                width="16"
                height="16"
                className="flex-shrink-0"
              />
              <span className="max-w-[90px] truncate">
                {thread.values?.targetRepository?.repo || "x"}
              </span>
              <span>/</span>
              <GitBranch className="size-2.5 flex-shrink-0" />
              <span className="max-w-[70px] truncate">
                {thread.values?.targetRepository?.branch || "x"}
              </span>
            </div>

            <span>•</span>

            <span className="mx-1 whitespace-nowrap">{displayDate}</span>

            {!isSidebar && (
              <>
                <span>•</span>
                <div className="ml-1 flex items-center gap-1">
                  <ListTodo className="size-4 flex-shrink-0" />
                  <span>
                    {completedTasks}/{totalTasks} tasks
                  </span>
                </div>
              </>
            )}
          </div>
          {isSidebar && (
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <ListTodo className="size-4 flex-shrink-0" />
              <span>
                {completedTasks}/{totalTasks} tasks
              </span>
            </div>
          )}
        </div>
        <ArrowRight
          className={cn(
            "mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-gray-400 opacity-0 transition-opacity group-hover:opacity-100",
          )}
        />
      </div>
    </div>
  );
});
