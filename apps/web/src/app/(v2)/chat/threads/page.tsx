"use client";

import { useState, Suspense, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ArrowLeft, Search, Filter } from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadMetadata } from "@/components/v2/types";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { ThreadCard, ThreadCardLoading } from "@/components/v2/thread-card";
import { ThemeToggle } from "@/components/theme-toggle";
import { InstallationSelector } from "@/components/github/installation-selector";
import { GitHubAppProvider, useGitHubAppProvider } from "@/providers/GitHubApp";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { useThreadsStatus } from "@/hooks/useThreadsStatus";
import { cn } from "@/lib/utils";
import { threadsToMetadata } from "@/lib/thread-utils";
import { UserPopover } from "@/components/user-popover";
import { OpenSWELogoSVG } from "@/components/icons/openswe";

type FilterStatus =
  | "all"
  | "running"
  | "completed"
  | "failed"
  | "pending"
  | "idle"
  | "paused"
  | "error";

function AllThreadsPageContent() {
  const router = useRouter();
  const limit = 25;
  const [offset, setOffset] = useState(0);
  const { currentInstallation, installationsLoading } = useGitHubAppProvider();
  const {
    threads,
    isLoading: threadsLoading,
    hasMore,
  } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    currentInstallation,
    pagination: {
      limit,
      offset,
      sortBy: "updated_at",
      sortOrder: "desc",
    },
  });
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<FilterStatus>("all");

  const threadsMetadata = useMemo(() => threadsToMetadata(threads), [threads]);

  const threadIds = threadsMetadata.map((thread) => thread.id);

  const {
    statusMap,
    taskPlanMap,
    statusCounts,
    isLoading: statusLoading,
  } = useThreadsStatus(threadIds, threads);

  const filteredThreads = useMemo(() => {
    return threadsMetadata.filter((thread: ThreadMetadata) => {
      const matchesSearch =
        thread.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        thread.repository.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesStatus =
        statusFilter === "all" || statusMap[thread.id] === statusFilter;

      return matchesSearch && matchesStatus;
    });
  }, [threadsMetadata, searchQuery, statusFilter, statusMap]);

  const groupedThreads = useMemo(() => {
    return {
      running: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "running",
      ),
      completed: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "completed",
      ),
      failed: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "failed",
      ),
      pending: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "pending",
      ),
      idle: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "idle",
      ),
      paused: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "paused",
      ),
      error: filteredThreads.filter(
        (thread: ThreadMetadata) => statusMap[thread.id] === "error",
      ),
    };
  }, [filteredThreads, statusMap]);

  // Show loading state if threads/status/installation requests are loading, and there are no
  // threads to display (conditional of the status filter)
  const showThreadsLoading =
    (threadsLoading || statusLoading || installationsLoading) &&
    (statusFilter === "all"
      ? Object.values(groupedThreads).flat().length === 0
      : filteredThreads.length === 0);
  const showNoThreads =
    filteredThreads.length === 0 &&
    !threadsLoading &&
    !statusLoading &&
    !showThreadsLoading;

  return (
    <div className="bg-background flex h-screen flex-col">
      {/* Header */}
      <div className="border-border bg-card border-b px-4 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:bg-muted hover:text-foreground h-6 w-6 p-0"
            onClick={() => router.push("/chat")}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex items-center gap-2">
            <OpenSWELogoSVG
              width={120}
              height={18}
            />
          </div>
          <div className="ml-auto flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">
                {filteredThreads.length} threads
              </span>
            </div>
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <UserPopover />
            </div>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="border-border bg-muted/50 border-b px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="relative max-w-md flex-1">
            <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 transform" />
            <Input
              placeholder="Search threads..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="border-border bg-background text-foreground placeholder:text-muted-foreground pl-10"
            />
          </div>
          <div className="flex items-center gap-1">
            <Filter className="text-muted-foreground h-4 w-4" />
            <span className="text-muted-foreground mr-2 text-xs">Filter:</span>
            {(
              [
                "all",
                "running",
                "completed",
                "failed",
                "pending",
                "idle",
                "paused",
                "error",
              ] as FilterStatus[]
            ).map((status) => (
              <Button
                key={status}
                variant={statusFilter === status ? "secondary" : "ghost"}
                size="sm"
                className={cn(
                  "h-7 text-xs",
                  statusFilter === status
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                onClick={() => setStatusFilter(status)}
              >
                {status === "all"
                  ? "All"
                  : status.charAt(0).toUpperCase() + status.slice(1)}
                <Badge
                  variant="secondary"
                  className="bg-muted/70 text-muted-foreground ml-1 text-xs"
                >
                  {statusCounts[status]}
                </Badge>
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl p-4">
          {statusFilter === "all" ? (
            <div className="space-y-6">
              {Object.entries(groupedThreads).map(([status, threads]) => {
                if (threads.length === 0) return null;
                return (
                  <div key={status}>
                    <div className="mb-3 flex items-center gap-2">
                      <h2 className="text-foreground text-base font-semibold capitalize">
                        {status} Threads
                      </h2>
                      <Badge
                        variant="secondary"
                        className="bg-muted/70 text-muted-foreground text-xs"
                      >
                        {threads.length}
                      </Badge>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                      {threads.map((thread) => (
                        <ThreadCard
                          key={thread.id}
                          thread={thread}
                          status={statusMap[thread.id]}
                          statusLoading={statusLoading}
                          taskPlan={taskPlanMap[thread.id]}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {filteredThreads.map((thread) => (
                <ThreadCard
                  key={thread.id}
                  thread={thread}
                  status={statusMap[thread.id]}
                  statusLoading={statusLoading}
                  taskPlan={taskPlanMap[thread.id]}
                />
              ))}
            </div>
          )}

          {showNoThreads && (
            <div className="py-12 text-center">
              <div className="text-muted-foreground mb-2">No threads found</div>
              <div className="text-muted-foreground/70 text-xs">
                {!threads || threads.length === 0
                  ? "No threads have been created yet"
                  : searchQuery
                    ? "Try adjusting your search query"
                    : "No threads match the selected filter"}
              </div>
            </div>
          )}

          {showThreadsLoading && (
            <div>
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-foreground text-base font-semibold capitalize">
                  Loading threads...
                </h2>
              </div>
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {Array.from({ length: 9 }).map((_, index) => (
                  <ThreadCardLoading key={`all-threads-loading-${index}`} />
                ))}
              </div>
            </div>
          )}
          {!showNoThreads && hasMore && (
            <div className="flex items-center justify-center">
              <Button
                variant="outline"
                onClick={() => setOffset((prev) => prev + limit)}
              >
                Load more
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AllThreadsPage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <GitHubAppProvider>
        <AllThreadsPageContent />
      </GitHubAppProvider>
    </Suspense>
  );
}
