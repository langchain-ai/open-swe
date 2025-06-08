"use client";

import { GitBranch, ArrowRight, ListTodo } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ThreadWithTasks } from "@/providers/Thread";
import { cn } from "@/lib/utils";
import { StatusIndicator } from "@/components/status-indicator";
import { Skeleton } from "@/components/ui/skeleton";
import { GitHubSVG } from "./icons/github";

interface ThreadItemProps {
  thread: ThreadWithTasks;
  onClick: (thread: ThreadWithTasks) => void;
  variant?: "sidebar" | "dashboard";
  className?: string;
}

export function ThreadItem({
  thread,
  onClick,
  variant = "dashboard",
  className,
}: ThreadItemProps) {
  const isSidebar = variant === "sidebar";

  const displayDate = new Date(thread.created_at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });

  // Check if thread data is still loading/incomplete
  const isLoading =
    !thread.threadTitle ||
    thread.threadTitle.includes("undefined") ||
    !thread.repository ||
    thread.repository === "Unknown Repository" ||
    thread.repository.includes("undefined");

  if (isLoading) {
    return (
      <div
        className={cn(
          "rounded-md border border-gray-200 bg-inherit p-1.5 shadow-sm",
          className,
        )}
      >
        <div className="flex items-start gap-1.5">
          <div className="min-w-0 flex-1">
            {/* Line 1: Status Indicator Skeleton + Title Skeleton */}
            <div className="flex items-center gap-1.5">
              <Skeleton className="h-3 w-3 rounded-full" />
              <Skeleton className="h-3 w-24" />
            </div>

            {/* Line 2: Repo/Branch Skeleton + Date Skeleton */}
            <div className="mt-1 flex items-center gap-1 text-xs">
              <Skeleton className="h-3 w-3" /> {/* GitHub icon placeholder */}
              <Skeleton className="h-3 w-16" /> {/* Repo */}
              <Skeleton className="ml-0.5 h-2.5 w-2.5" />{" "}
              {/* Branch icon placeholder */}
              <Skeleton className="ml-0.5 h-3 w-12" /> {/* Branch */}
              <Skeleton className="ml-auto h-3 w-10" /> {/* Date */}
            </div>

            {/* Line 3: Tasks Badge Skeleton */}
            <div className="mt-1">
              <Skeleton className="h-4 w-20 rounded" />
            </div>
          </div>
          {/* ArrowRight skeleton is usually omitted */}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group cursor-pointer rounded-md border border-gray-200 bg-inherit p-2 shadow-sm transition-colors hover:bg-gray-50 hover:shadow-md",
        className,
      )}
      onClick={() => onClick(thread)}
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
}
