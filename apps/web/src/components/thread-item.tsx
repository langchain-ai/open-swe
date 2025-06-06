"use client";
import {
  CheckCircle2,
  XCircle,
  LoaderCircle,
  Pause,
  Github,
  GitBranch,
  ArrowRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ThreadSummary } from "@/types/index";
import { cn } from "@/lib/utils";

// StatusIndicator component
const StatusIndicator = ({
  status,
  size = "default",
}: {
  status: "running" | "interrupted" | "done" | "error";
  size?: "default" | "sm";
}) => {
  const iconClass = size === "sm" ? "h-3 w-3" : "h-4 w-4";

  switch (status) {
    case "running":
      return (
        <LoaderCircle className={`${iconClass} animate-spin text-blue-500`} />
      );
    case "interrupted":
      return <Pause className={`${iconClass} text-yellow-500`} />;
    case "done":
      return <CheckCircle2 className={`${iconClass} text-green-500`} />;
    case "error":
      return <XCircle className={`${iconClass} text-red-500`} />;
    default:
      return null;
  }
};

interface ThreadItemProps {
  thread: ThreadSummary;
  onClick: (thread: ThreadSummary) => void;
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
              <span className={cn(isSidebar ? "max-w-[60px] truncate" : "")}>
                {thread.repository}
              </span>
              <span>/</span>
              <GitBranch className={isSidebar ? "h-2.5 w-2.5" : "h-3 w-3"} />
              <span className={cn(isSidebar ? "max-w-[40px] truncate" : "")}>
                {thread.branch}
              </span>
            </div>
            {!isSidebar && <span>{thread.date}</span>}
          </div>
          {isSidebar && (
            <div className="mt-1 flex items-center gap-2 text-xs">
              <span className="text-gray-400">{thread.date}</span>
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
