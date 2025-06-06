"use client";
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  PanelRightOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTasks } from "@/providers/Task";
import { ThreadSummary } from "@/types/index";
import { useQueryState, parseAsString } from "nuqs";
import { useState } from "react";
import { ThreadItem } from "./thread-item";
import { groupTasksIntoThreads, sortThreadsByDate } from "@/lib/thread-utils";

const THREADS_PER_PAGE = 10; // More threads per page in sidebar

// TODO: Clarify Language about Threads and Tasks in the TaskListSidebar component
interface TaskListSidebarProps {
  onCollapse?: () => void;
}

export default function TaskListSidebar({ onCollapse }: TaskListSidebarProps) {
  const [taskId, setTaskId] = useQueryState("taskId", parseAsString);
  const [threadId, setThreadId] = useQueryState("threadId", parseAsString);
  const [currentPage, setCurrentPage] = useState(0);
  const { allTasks, tasksLoading } = useTasks();

  // Handle thread navigation
  const handleThreadClick = (thread: ThreadSummary) => {
    setThreadId(thread.threadId);
    setTaskId(null);
  };

  const threadSummaries = groupTasksIntoThreads(allTasks);
  const sortedThreads = sortThreadsByDate(threadSummaries);
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
              <ThreadItem
                key={thread.threadId}
                thread={thread}
                onClick={handleThreadClick}
                variant="sidebar"
              />
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
