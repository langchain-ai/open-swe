"use client";

import type React from "react";
import { useState, Suspense } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ArrowLeft, Search, Filter } from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { ThreadCard, ThreadCardLoading } from "@/components/v2/thread-card";
import { ThemeToggle } from "@/components/theme-toggle";
import { InstallationSelector } from "@/components/github/installation-selector";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

type FilterStatus = "all" | "running" | "completed" | "failed" | "pending";

function AllThreadsPageContent() {
  const router = useRouter();
  const { threads, isLoading: threadsLoading } = useThreadsSWR<GraphState>({
    assistantId: MANAGER_GRAPH_ID,
    refreshInterval: 15000, // Poll every 15 seconds
  });
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<FilterStatus>("all");

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] = threads.map(threadToDisplayInfo);

  // Filter and search threads
  const filteredThreads = displayThreads.filter((thread) => {
    const matchesSearch =
      thread.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      thread.repository.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus =
      statusFilter === "all" || thread.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  // Group threads by status
  const groupedThreads = {
    running: filteredThreads.filter((t) => t.status === "running"),
    completed: filteredThreads.filter((t) => t.status === "completed"),
    failed: filteredThreads.filter((t) => t.status === "failed"),
    pending: filteredThreads.filter((t) => t.status === "pending"),
  };

  const statusCounts = {
    all: displayThreads.length,
    running: displayThreads.filter((t) => t.status === "running").length,
    completed: displayThreads.filter((t) => t.status === "completed").length,
    failed: displayThreads.filter((t) => t.status === "failed").length,
    pending: displayThreads.filter((t) => t.status === "pending").length,
  };

  return (
    <GitHubAppProvider>
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
              <div className="h-2 w-2 rounded-full bg-green-500"></div>
              <span className="text-muted-foreground font-mono text-sm">
                All Threads
              </span>
            </div>
            <div className="ml-auto flex items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground text-xs">
                  {filteredThreads.length} threads
                </span>
              </div>
              <div className="flex items-center gap-2">
                <InstallationSelector />
                <ThemeToggle />
              </div>
            </div>
          </div>
        </div>

        {/* Search and Filters */}
        <div className="border-border bg-muted/50 border-b px-4 py-3 dark:bg-gray-950">
          <div className="flex items-center gap-3">
            <div className="relative max-w-md flex-1">
              <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 transform" />
              <Input
                placeholder="Search threads..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="border-border bg-background text-foreground placeholder:text-muted-foreground pl-10 dark:bg-gray-900"
              />
            </div>
            <div className="flex items-center gap-1">
              <Filter className="text-muted-foreground h-4 w-4" />
              <span className="text-muted-foreground mr-2 text-xs">
                Filter:
              </span>
              {(
                [
                  "all",
                  "running",
                  "completed",
                  "failed",
                  "pending",
                ] as FilterStatus[]
              ).map((status) => (
                <Button
                  key={status}
                  variant={statusFilter === status ? "secondary" : "ghost"}
                  size="sm"
                  className={`h-7 text-xs ${
                    statusFilter === status
                      ? "bg-muted text-foreground dark:bg-gray-700"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  }`}
                  onClick={() => setStatusFilter(status)}
                >
                  {status === "all"
                    ? "All"
                    : status.charAt(0).toUpperCase() + status.slice(1)}
                  <Badge
                    variant="secondary"
                    className="bg-muted/70 text-muted-foreground ml-1 text-xs dark:bg-gray-800"
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
              // Show grouped view when "all" is selected
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
                          className="bg-muted/70 text-muted-foreground text-xs dark:bg-gray-800"
                        >
                          {threads.length}
                        </Badge>
                      </div>
                      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                        {threads.map((thread) => (
                          <ThreadCard
                            key={thread.id}
                            thread={thread}
                          />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              // Show flat list when specific status is selected
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {filteredThreads.map((thread) => (
                  <ThreadCard
                    key={thread.id}
                    thread={thread}
                  />
                ))}
              </div>
            )}

            {filteredThreads.length === 0 && !threadsLoading && (
              <div className="py-12 text-center">
                <div className="text-muted-foreground mb-2">
                  No threads found
                </div>
                <div className="text-muted-foreground/70 text-xs">
                  {searchQuery
                    ? "Try adjusting your search query"
                    : "No threads match the selected filter"}
                </div>
              </div>
            )}

            {threadsLoading && threads.length === 0 && (
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
          </div>
        </div>
      </div>
    </GitHubAppProvider>
  );
}

export default function AllThreadsPage() {
  return (
    <Suspense
      fallback={
        <div className="bg-background flex h-screen flex-col">
          <div className="border-border bg-card border-b px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="bg-muted h-6 w-6 animate-pulse rounded"></div>
              <div className="flex items-center gap-2">
                <div className="h-2 w-2 rounded-full bg-green-500"></div>
                <span className="text-muted-foreground font-mono text-sm">
                  All Threads
                </span>
              </div>
            </div>
          </div>
          <div className="flex-1 overflow-auto">
            <div className="mx-auto max-w-6xl p-4">
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {Array.from({ length: 9 }).map((_, index) => (
                  <ThreadCardLoading key={`loading-${index}`} />
                ))}
              </div>
            </div>
          </div>
        </div>
      }
    >
      <AllThreadsPageContent />
    </Suspense>
  );
}
