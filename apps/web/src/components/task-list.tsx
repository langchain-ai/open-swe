"use client";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Archive, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTasks } from "@/providers/Task";
import { ThreadSummary } from "@/types/index";
import { useQueryState, parseAsString } from "nuqs";
import { useState } from "react";
import { ThreadItem } from "./thread-item";
import { groupTasksIntoThreads, sortThreadsByDate } from "@/lib/thread-utils";

const THREADS_PER_PAGE = 5;

// TODO: abstract out similar logic in task-list and task-list-sidebar
export default function TaskList() {
  const [taskId, setTaskId] = useQueryState("taskId", parseAsString);
  const [threadId, setThreadId] = useQueryState("threadId", parseAsString);
  const [currentPage, setCurrentPage] = useState(0);
  const { allTasks, tasksLoading } = useTasks();

  const isDashboardMode = !taskId;

  const handleThreadClick = (thread: ThreadSummary) => {
    setThreadId(thread.threadId);
    setTaskId(null);
  };

  if (!isDashboardMode) {
    return null;
  }

  const threadSummaries = groupTasksIntoThreads(allTasks);
  const sortedThreads = sortThreadsByDate(threadSummaries);

  const totalThreads = sortedThreads.length;
  const totalPages = Math.ceil(totalThreads / THREADS_PER_PAGE);
  const startIndex = currentPage * THREADS_PER_PAGE;
  const endIndex = startIndex + THREADS_PER_PAGE;
  const paginatedThreads = sortedThreads.slice(startIndex, endIndex);

  return (
    <div className="flex h-full w-full flex-col">
      <div className="mx-auto w-full max-w-3xl p-4">
        <Tabs
          defaultValue="threads"
          className="flex h-full flex-col"
        >
          <TabsList className="mb-4 grid h-auto w-fit grid-cols-2 bg-transparent p-0">
            <TabsTrigger
              value="threads"
              className="px-0 pb-3 font-medium data-[state=active]:rounded-none data-[state=active]:border-b-2 data-[state=active]:border-b-black data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              Threads ({totalThreads})
            </TabsTrigger>
            <TabsTrigger
              value="archive"
              className="ml-6 px-0 pb-3 font-medium text-gray-500 data-[state=active]:rounded-none data-[state=active]:border-b-2 data-[state=active]:border-b-black data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              Archived
            </TabsTrigger>
          </TabsList>

          <div className="mb-4 border-b border-gray-200"></div>

          <TabsContent
            value="threads"
            className="mt-0 flex-1 overflow-hidden"
          >
            {tasksLoading ? (
              <div className="flex h-full items-center justify-center">
                <div className="text-center text-gray-500">
                  <Archive className="mx-auto mb-2 h-6 w-6 animate-pulse opacity-50" />
                  <p className="text-sm">Loading threads...</p>
                </div>
              </div>
            ) : paginatedThreads.length > 0 ? (
              <div className="space-y-4">
                {paginatedThreads.map((thread) => (
                  <ThreadItem
                    key={thread.threadId}
                    thread={thread}
                    onClick={handleThreadClick}
                    variant="dashboard"
                  />
                ))}

                {/* Pagination Controls */}
                {totalPages > 1 && (
                  <div className="flex items-center justify-between border-t border-gray-200 pt-4">
                    <div className="text-sm text-gray-500">
                      Showing {startIndex + 1}-
                      {Math.min(endIndex, totalThreads)} of {totalThreads}{" "}
                      threads
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setCurrentPage((p) => Math.max(0, p - 1))
                        }
                        disabled={currentPage === 0}
                        className="h-8 w-8 p-0"
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </Button>
                      <span className="px-3 py-1 text-sm text-gray-500">
                        {currentPage + 1} / {totalPages}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setCurrentPage((p) => Math.min(totalPages - 1, p + 1))
                        }
                        disabled={currentPage === totalPages - 1}
                        className="h-8 w-8 p-0"
                      >
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
          </TabsContent>

          <TabsContent
            value="archive"
            className="mt-0 flex-1"
          >
            <div className="flex h-full items-center justify-center">
              <div className="text-center text-gray-500">
                <Archive className="mx-auto mb-2 h-6 w-6 opacity-50" />
                <p className="text-sm">Archive feature coming soon</p>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
