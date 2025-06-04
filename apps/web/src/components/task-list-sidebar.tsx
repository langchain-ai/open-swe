"use client";
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  XCircle,
  LoaderCircle,
  Pause,
  Github,
  GitBranch,
  PanelRightOpen,
  ArrowRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTasks, ThreadSummary } from "@/providers/Task";
import { useQueryState, parseAsString } from "nuqs";
import { useEffect, useState } from "react";

const THREADS_PER_PAGE = 10; // More threads per page in sidebar

// StatusIndicator component for sidebar
const StatusIndicator = ({
  status,
}: {
  status: "running" | "interrupted" | "done" | "error";
}) => {
  switch (status) {
    case "running":
      return <LoaderCircle className="h-3 w-3 animate-spin text-blue-500" />;
    case "interrupted":
      return <Pause className="h-3 w-3 text-yellow-500" />;
    case "done":
      return <CheckCircle2 className="h-3 w-3 text-green-500" />;
    case "error":
      return <XCircle className="h-3 w-3 text-red-500" />;
    default:
      return null;
  }
};

interface TaskListSidebarProps {
  onCollapse?: () => void;
}

export default function TaskListSidebar({ onCollapse }: TaskListSidebarProps) {
  const [taskId, setTaskId] = useQueryState("taskId", parseAsString);
  const [threadId, setThreadId] = useQueryState("threadId", parseAsString);
  const [currentPage, setCurrentPage] = useState(0);
  const { getAllTasks, allTasks, setAllTasks, tasksLoading, setTasksLoading } =
    useTasks();

  // Fetch all tasks for sidebar
  useEffect(() => {
    if (typeof window === "undefined") return;
    setTasksLoading(true);
    getAllTasks()
      .then(setAllTasks)
      .catch(console.error)
      .finally(() => setTasksLoading(false));
  }, [getAllTasks, setAllTasks, setTasksLoading]);

  // Handle thread navigation (navigate to chat mode with thread loaded)
  const handleThreadClick = (thread: ThreadSummary) => {
    setThreadId(thread.threadId);
    setTaskId(null); // Clear task selection to show thread in chat mode
  };

  // Group tasks by thread
  const threadSummaries: ThreadSummary[] = allTasks.reduce((acc, task) => {
    const existingThread = acc.find((t) => t.threadId === task.threadId);

    if (existingThread) {
      existingThread.tasks.push(task);
      existingThread.totalTasksCount += 1;
      if (task.status === "done") {
        existingThread.completedTasksCount += 1;
      }
    } else {
      acc.push({
        threadId: task.threadId,
        threadTitle:
          task.threadTitle || `Thread ${task.threadId.substring(0, 8)}`,
        repository: task.repository || "Unknown Repository",
        branch: task.branch || "main",
        date: task.date,
        createdAt: task.createdAt,
        tasks: [task],
        completedTasksCount: task.status === "done" ? 1 : 0,
        totalTasksCount: 1,
        status: task.status,
      });
    }

    return acc;
  }, [] as ThreadSummary[]);

  // Determine overall thread status
  threadSummaries.forEach((thread) => {
    const hasRunning = thread.tasks.some((t) => t.status === "running");
    const hasError = thread.tasks.some((t) => t.status === "error");
    const allDone = thread.tasks.every((t) => t.status === "done");

    if (hasError) {
      thread.status = "error";
    } else if (hasRunning) {
      thread.status = "running";
    } else if (allDone) {
      thread.status = "done";
    } else {
      thread.status = "interrupted";
    }
  });

  // Sort threads by creation date (newest first)
  const sortedThreads = threadSummaries.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  );

  const totalThreads = sortedThreads.length;
  const totalPages = Math.ceil(totalThreads / THREADS_PER_PAGE);
  const startIndex = currentPage * THREADS_PER_PAGE;
  const endIndex = startIndex + THREADS_PER_PAGE;
  const paginatedThreads = sortedThreads.slice(startIndex, endIndex);

  return (
    <div className="flex h-full w-full flex-col border-r bg-white">
      <div className="border-b border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Threads</h2>
            <p className="text-sm text-gray-500">
              {totalThreads} total threads
            </p>
          </div>
          {onCollapse && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onCollapse}
              className="h-8 w-8 p-0 hover:bg-gray-100"
            >
              <PanelRightOpen className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {tasksLoading ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-gray-500">
              <Archive className="mx-auto mb-2 h-5 w-5 animate-pulse opacity-50" />
              <p className="text-sm">Loading threads...</p>
            </div>
          </div>
        ) : paginatedThreads.length > 0 ? (
          <div className="h-full space-y-2 overflow-y-auto p-3 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
            {paginatedThreads.map((thread) => (
              <div
                key={thread.threadId}
                className="cursor-pointer rounded-md border border-gray-200 bg-white p-3 transition-colors hover:bg-gray-50"
                onClick={() => handleThreadClick(thread)}
              >
                <div className="flex items-center gap-2">
                  <div className="mt-0.5 flex-shrink-0">
                    <StatusIndicator status={thread.status} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h4 className="truncate text-xs leading-tight font-medium text-gray-900">
                      {thread.threadTitle}
                    </h4>
                    <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
                      <div className="flex items-center gap-1">
                        <Github className="h-2.5 w-2.5" />
                        <span className="max-w-[60px] truncate">
                          {thread.repository}
                        </span>
                        <span>/</span>
                        <GitBranch className="h-2.5 w-2.5" />
                        <span className="max-w-[40px] truncate">
                          {thread.branch}
                        </span>
                      </div>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-xs">
                      <span className="text-gray-400">{thread.date}</span>
                      <Badge
                        variant="outline"
                        className="px-1 py-0 text-xs"
                      >
                        {thread.completedTasksCount}/{thread.totalTasksCount}
                      </Badge>
                    </div>
                  </div>
                  <ArrowRight className="h-3 w-3 text-gray-400 opacity-0 transition-opacity group-hover:opacity-100" />
                </div>
              </div>
            ))}

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="border-t border-gray-200 px-1 pt-3">
                <div className="flex items-center justify-between text-xs">
                  <div className="text-gray-500">
                    {startIndex + 1}-{Math.min(endIndex, totalThreads)} of{" "}
                    {totalThreads}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-6 w-6 p-0"
                      onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
                      disabled={currentPage === 0}
                    >
                      <ChevronLeft className="h-3 w-3" />
                    </Button>
                    <span className="px-2 text-gray-500">
                      {currentPage + 1}/{totalPages}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-6 w-6 p-0"
                      onClick={() =>
                        setCurrentPage((p) => Math.min(totalPages - 1, p + 1))
                      }
                      disabled={currentPage === totalPages - 1}
                    >
                      <ChevronRight className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-gray-500">
              <Archive className="mx-auto mb-2 h-5 w-5 opacity-50" />
              <p className="text-sm">No threads found</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
