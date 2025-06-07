"use client";
import { Github, GitBranch, ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ThreadWithTasks } from "@/providers/Thread";
import { cn } from "@/lib/utils";
import { StatusIndicator } from "@/components/status-indicator";
import { Skeleton } from "@/components/ui/skeleton";

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
          "cursor-pointer rounded-md border border-gray-200 bg-white p-3 transition-colors hover:bg-gray-50",
          isSidebar
            ? "hover:bg-gray-50"
            : "rounded-lg p-4 shadow-sm transition-shadow hover:shadow-md",
          className,
        )}
      >
        <div className={cn("flex items-center gap-2", !isSidebar && "gap-3")}>
          <div className={cn("flex-shrink-0", isSidebar ? "mt-0.5" : "mt-1")}>
            <Skeleton
              className={cn("rounded-full", isSidebar ? "h-3 w-3" : "h-4 w-4")}
            />
          </div>
          <div className="min-w-0 flex-1">
            <Skeleton
              className={cn("mb-2", isSidebar ? "h-3 w-20" : "h-4 w-32")}
            />
            <div
              className={cn(
                "flex items-center gap-2",
                isSidebar ? "gap-1" : "gap-2",
              )}
            >
              <Skeleton className={isSidebar ? "h-2 w-16" : "h-3 w-20"} />
              <Skeleton className={isSidebar ? "h-2 w-2" : "h-3 w-3"} />
              <Skeleton className={isSidebar ? "h-2 w-12" : "h-3 w-16"} />
            </div>
            {isSidebar && (
              <div className="mt-1 flex items-center gap-2">
                <Skeleton className="h-2 w-8" />
                <Skeleton className="h-3 w-6" />
              </div>
            )}
            {!isSidebar && (
              <div className="mt-2">
                <Skeleton className="h-3 w-24" />
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "cursor-pointer rounded-md border border-gray-200 bg-white p-3 transition-colors hover:bg-gray-50",
        isSidebar
          ? "hover:bg-gray-50"
          : "rounded-lg p-4 shadow-sm transition-shadow hover:shadow-md",
        className,
      )}
      onClick={() => onClick(thread)}
    >
      <div className={cn("flex items-center gap-2", !isSidebar && "gap-3")}>
        <div className={cn("flex-shrink-0", isSidebar ? "mt-0.5" : "mt-1")}>
          <StatusIndicator
            status={thread.status}
            size={isSidebar ? "sm" : "default"}
          />
        </div>
        <div className="min-w-0 flex-1">
          <h4
            className={cn(
              "truncate font-medium text-gray-900",
              isSidebar ? "text-xs leading-tight" : "text-sm",
            )}
          >
            {thread.threadTitle}
          </h4>
          <div
            className={cn(
              "flex items-center gap-2 text-gray-500",
              isSidebar ? "mt-1 text-xs" : "mt-2 gap-4 text-xs",
            )}
          >
            <div className="flex items-center gap-1">
              <Github className={isSidebar ? "h-2.5 w-2.5" : "h-3 w-3"} />
              <span className={cn(isSidebar ? "max-w-[100px] truncate" : "")}>
                {thread.repository}
              </span>
              <span>/</span>
              <GitBranch className={isSidebar ? "h-2.5 w-2.5" : "h-3 w-3"} />
              <span className={cn(isSidebar ? "max-w-[80px] truncate" : "")}>
                {thread.branch}
              </span>
            </div>
            {!isSidebar && <span>{displayDate}</span>}
          </div>
          {isSidebar && (
            <div className="mt-1 flex items-center gap-2 text-xs">
              <span className="text-gray-400">{displayDate}</span>
              <Badge
                variant="outline"
                className="px-1 py-0 text-xs"
              >
                {thread.completedTasksCount}/{thread.totalTasksCount}
              </Badge>
            </div>
          )}
          {!isSidebar && (
            <div className="mt-2 flex items-center gap-4 text-xs text-gray-500">
              <Badge
                variant="outline"
                className="text-xs"
              >
                {thread.completedTasksCount}/{thread.totalTasksCount} tasks
                completed
              </Badge>
            </div>
          )}
        </div>
        <ArrowRight
          className={cn(
            "text-gray-400 opacity-0 transition-opacity group-hover:opacity-100",
            isSidebar ? "h-3 w-3" : "h-4 w-4",
          )}
        />
      </div>
    </div>
  );
}
