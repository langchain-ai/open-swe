"use client";

import { Archive, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useThreads, ThreadWithTasks } from "@/providers/Thread";
import { useQueryState, parseAsString } from "nuqs";
import { useState } from "react";
import { ThreadItem } from "./thread-item";

const THREADS_PER_PAGE = 5;

export default function TaskList() {
  const [taskId, setTaskId] = useQueryState("taskId", parseAsString);
  const [_threadId, setThreadId] = useQueryState("threadId", parseAsString);
  const [currentPage, setCurrentPage] = useState(0);
  const { threads, threadsLoading } = useThreads();

  const isDashboardMode = !taskId;

  const handleThreadClick = (thread: ThreadWithTasks) => {
    setThreadId(thread.thread_id);
    setTaskId(null);
  };

  if (!isDashboardMode) {
    return null;
  }

  // Threads are already sorted by creation date in ThreadProvider
  const sortedThreads = threads;
  const totalThreads = sortedThreads.length;
  const totalPages = Math.ceil(totalThreads / THREADS_PER_PAGE);
  const startIndex = currentPage * THREADS_PER_PAGE;
  const endIndex = startIndex + THREADS_PER_PAGE;
  const paginatedThreads = sortedThreads.slice(startIndex, endIndex);

  return (
    <div className="flex h-full w-full max-w-3xl flex-col gap-2">
      <p className="text-sm text-gray-500">Threads ({totalThreads})</p>
      <div>
        {threadsLoading ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-gray-500">
              <Archive className="mx-auto mb-2 h-6 w-6 animate-pulse opacity-50" />
              <p className="text-sm">Loading threads...</p>
            </div>
          </div>
        ) : paginatedThreads.length > 0 ? (
          <div className="space-y-2">
            {paginatedThreads.map((thread) => (
              <ThreadItem
                key={thread.thread_id}
                thread={thread}
                onClick={handleThreadClick}
                variant="dashboard"
              />
            ))}

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between border-t pt-4">
                <div className="text-sm text-gray-500">
                  Showing {startIndex + 1}-{Math.min(endIndex, totalThreads)} of{" "}
                  {totalThreads} threads
                </div>
                <div className="flex items-center space-x-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
                    disabled={currentPage === 0}
                  >
                    <ChevronLeft className="h-4 w-4" />
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setCurrentPage(Math.min(totalPages - 1, currentPage + 1))
                    }
                    disabled={currentPage === totalPages - 1}
                  >
                    Next
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-gray-500">
              <Archive className="mx-auto mb-2 h-6 w-6 opacity-50" />
              <p className="text-sm">No threads found</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
