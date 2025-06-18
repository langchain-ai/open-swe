"use client";
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  PanelRightOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useThreadsContext } from "@/providers/Thread";
import { useQueryState, parseAsString } from "nuqs";
import { useState, useCallback } from "react";
import { ThreadItem } from "./thread-item";
import { Thread } from "@langchain/langgraph-sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";

const THREADS_PER_PAGE = 10;

interface TaskListSidebarProps {
  onCollapse?: () => void;
}

export default function TaskListSidebar({ onCollapse }: TaskListSidebarProps) {
  const [threadId, setThreadId] = useQueryState("threadId", parseAsString);
  const [currentPage, setCurrentPage] = useState(0);
  const { threads, threadsLoading, handleThreadClick } = useThreadsContext();

  const onThreadClick = useCallback(
    (thread: Thread<GraphState>) => {
      handleThreadClick(thread, threadId, setThreadId);
    },
    [handleThreadClick, threadId, setThreadId],
  );
  // Sort threads by creation date (newest first) TODO use provider (already done there)
  const sortedThreads = threads;
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
        {threadsLoading && threads.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-gray-500">
              <Archive className="mx-auto mb-2 h-5 w-5 animate-pulse opacity-50" />
              <p className="text-sm">Loading threads...</p>
            </div>
          </div>
        ) : paginatedThreads.length > 0 ? (
          <div className="h-full overflow-y-auto">
            <div className="space-y-1 p-2">
              {paginatedThreads.map((thread) => (
                <ThreadItem
                  key={thread.thread_id}
                  thread={thread}
                  onClick={onThreadClick}
                  variant="sidebar"
                />
              ))}
            </div>
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

      {totalPages > 1 && (
        <div className="border-t border-gray-200 p-3">
          <div className="flex items-center justify-between">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
              disabled={currentPage === 0}
              className="h-7"
            >
              <ChevronLeft className="h-3 w-3" />
            </Button>
            <span className="text-xs text-gray-500">
              Page {currentPage + 1} of {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setCurrentPage(Math.min(totalPages - 1, currentPage + 1))
              }
              disabled={currentPage === totalPages - 1}
              className="h-7"
            >
              <ChevronRight className="h-3 w-3" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
