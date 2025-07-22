"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FilePlus2, Archive, Zap } from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadMetadata } from "./types";
import { TerminalInput } from "./terminal-input";
import { useFileUpload } from "@/hooks/useFileUpload";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { Label } from "../ui/label";
import { ContentBlocksPreview } from "../thread/ContentBlocksPreview";
import { ThemeToggle } from "../theme-toggle";
import { ThreadCard, ThreadCardLoading } from "./thread-card";
import { GitHubInstallationBanner } from "../github/installation-banner";
import { QuickActions } from "./quick-actions";
import { DraftsSection } from "./drafts-section";
import { GitHubLogoutButton } from "../github/github-oauth-button";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import { InstallationSelector } from "../github/installation-selector";

import { useThreadsStatus } from "@/hooks/useThreadsStatus";
import { Thread } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { useState, useMemo } from "react";
import { threadsToMetadata } from "@/lib/thread-utils";
import { Settings } from "lucide-react";
import NextLink from "next/link";
import { useTaskPlanContext } from "@/providers/TaskPlanProvider";

function OpenSettingsButton() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger>
          <NextLink href="/settings">
            <Settings className="size-4" />
          </NextLink>
        </TooltipTrigger>
        <TooltipContent side="bottom">Open Settings</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

interface DefaultViewProps {
  threads: Thread<ManagerGraphState>[];
  threadsLoading: boolean;
}

export function DefaultView({ threads, threadsLoading }: DefaultViewProps) {
  const router = useRouter();
  const [quickActionPrompt, setQuickActionPrompt] = useState("");
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const [draftToLoad, setDraftToLoad] = useState("");
  const assistantId: string | undefined = MANAGER_GRAPH_ID;
  const {
    contentBlocks,
    setContentBlocks,
    handleFileUpload,
    dropRef,
    removeBlock,
    dragOver,
    handlePaste,
  } = useFileUpload();
  const [autoAccept, setAutoAccept] = useState(false);

  const { getTaskPlan } = useTaskPlanContext();

  const threadsMetadata = useMemo(() => threadsToMetadata(threads), [threads]);
  const displayThreads = threadsMetadata.slice(0, 4);
  const displayThreadIds = displayThreads.map((thread) => thread.id);

  const { statusMap, isLoading: statusLoading } = useThreadsStatus(
    displayThreadIds,
    threads,
  );

  const handleLoadDraft = (content: string) => {
    setDraftToLoad(content);
  };

  if (!apiUrl) {
    return <div>Missing API URL environment variable</div>;
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* Header */}
      <div className="border-border bg-card border-b px-4 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-green-500"></div>
            <span className="text-muted-foreground font-mono text-sm">
              Open SWE
            </span>
          </div>
          <div className="flex items-center gap-4">
            <InstallationSelector />
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">ready</span>
              <div className="h-1 w-1 rounded-full bg-green-500 dark:bg-green-600"></div>
            </div>
            <OpenSettingsButton />
            <ThemeToggle />
            <GitHubLogoutButton />
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-6 p-4">
          <GitHubInstallationBanner />
          {/* Terminal Chat Input */}
          <Card
            className={cn(
              "border-border bg-card py-0 dark:bg-gray-950",
              dragOver
                ? "border-primary border-2 border-dotted"
                : "border border-solid",
            )}
            ref={dropRef}
          >
            <CardContent className="p-4">
              <ContentBlocksPreview
                blocks={contentBlocks}
                onRemove={removeBlock}
              />
              <input
                id="file-input"
                type="file"
                onChange={handleFileUpload}
                multiple
                accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                className="hidden"
              />
              <div className="space-y-3">
                <TerminalInput
                  placeholder="Describe your coding task or ask a question..."
                  apiUrl={apiUrl}
                  assistantId={assistantId}
                  contentBlocks={contentBlocks}
                  setContentBlocks={setContentBlocks}
                  onPaste={handlePaste}
                  quickActionPrompt={quickActionPrompt}
                  setQuickActionPrompt={setQuickActionPrompt}
                  draftToLoad={draftToLoad}
                  autoAcceptPlan={autoAccept}
                  setAutoAcceptPlan={setAutoAccept}
                />
                <div className="flex items-center gap-2">
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger>
                        <Label
                          htmlFor="file-input"
                          className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center justify-center rounded-full bg-inherit"
                        >
                          <FilePlus2 className="size-4" />
                        </Label>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        Attach files
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <TooltipIconButton
                    variant={autoAccept ? "brand" : "ghost"}
                    tooltip="Automatically accept the plan"
                    className={cn(
                      autoAccept
                        ? "text-secondary"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setAutoAccept((prev) => !prev)}
                    side="bottom"
                  >
                    <Zap className="size-4" />
                  </TooltipIconButton>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Recent & Running Threads */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-foreground text-base font-semibold">
                Recent & Running Threads
              </h2>
              <Button
                variant="outline"
                size="sm"
                className="border-border text-muted-foreground hover:text-foreground h-7 text-xs"
                onClick={() => router.push("/chat/threads")}
              >
                View All
              </Button>
            </div>

            {threadsLoading || threads.length ? (
              <div className="grid gap-3 md:grid-cols-2">
                {threadsLoading && threads.length === 0 && (
                  <>
                    <ThreadCardLoading />
                    <ThreadCardLoading />
                    <ThreadCardLoading />
                    <ThreadCardLoading />
                  </>
                )}
                {displayThreads.map((thread) => (
                  <ThreadCard
                    key={thread.id}
                    thread={thread}
                    status={statusMap[thread.id]}
                    statusLoading={statusLoading}
                    taskPlan={getTaskPlan(thread.id)}
                  />
                ))}
              </div>
            ) : (
              <div className="flex items-center justify-center py-8">
                <span className="text-muted-foreground flex items-center gap-2">
                  <Archive className="size-4" />
                  <span className="text-sm">No threads found</span>
                </span>
              </div>
            )}
          </div>
          <QuickActions setQuickActionPrompt={setQuickActionPrompt} />
          {/* TODO: Better multiple draft handling. Not actually used right now */}
          <DraftsSection onLoadDraft={handleLoadDraft} />
        </div>
      </div>
    </div>
  );
}
