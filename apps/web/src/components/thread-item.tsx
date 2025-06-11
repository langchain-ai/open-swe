"use client";
import { memo } from "react";
import { GitBranch, ArrowRight, ListTodo } from "lucide-react";
import { ThreadWithTasks, useThreads } from "@/providers/Thread";
import { cn } from "@/lib/utils";
import { StatusIndicator } from "@/components/status-indicator";
import { GitHubSVG } from "./icons/github";
import { useQueryState } from "nuqs";

interface ThreadItemProps {
  thread: ThreadWithTasks;
  onClick: (thread: ThreadWithTasks) => void;
  variant?: "sidebar" | "dashboard";
  className?: string;
}

export const ThreadItem = memo(function ThreadItem({
  thread,
  onClick,
  variant = "dashboard",
  className,
}: ThreadItemProps) {
  const [threadId] = useQueryState("threadId");
  const { recentlyUpdatedThreads } = useThreads();
  const isSelected = thread.thread_id === threadId;
  const isSidebar = variant === "sidebar";
  const isRecentlyUpdated = recentlyUpdatedThreads.has(thread.thread_id);

  const displayDate = new Date(thread.created_at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });

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
              {thread.threadTitle}
            </h4>
          </div>

          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-gray-500">
            <div className="mr-1 flex flex-row items-center justify-start gap-1">
              <GitHubSVG
                width="16"
                height="16"
                className="flex-shrink-0"
              />
              <span className="max-w-[90px] truncate">{thread.repository}</span>
              <span>/</span>
              <GitBranch className="size-2.5 flex-shrink-0" />
              <span className="max-w-[70px] truncate">{thread.branch}</span>
            </div>

            <span>•</span>

            <span className="mx-1 whitespace-nowrap">{displayDate}</span>

            {!isSidebar && (
              <>
                <span>•</span>
                <div className="ml-1 flex items-center gap-1">
                  <ListTodo className="size-4 flex-shrink-0" />
                  <span>
                    {thread.completedTasksCount}/{thread.totalTasksCount} tasks
                  </span>
                </div>
              </>
            )}
          </div>
          {isSidebar && (
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <ListTodo className="size-4 flex-shrink-0" />
              <span>
                {thread.completedTasksCount}/{thread.totalTasksCount} tasks
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
