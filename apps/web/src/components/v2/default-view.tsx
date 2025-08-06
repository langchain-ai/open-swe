"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Archive, ListChecks } from "lucide-react";
import { useRouter } from "next/navigation";
import { TerminalInput } from "./terminal-input";
import { useFileUpload } from "@/hooks/useFileUpload";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { ContentBlocksPreview } from "../thread/ContentBlocksPreview";
import { ThemeToggle } from "../theme-toggle";
import { ThreadCard, ThreadCardLoading } from "./thread-card";
import { GitHubInstallationBanner } from "../github/installation-banner";
import { ApiKeyBanner } from "../api-key-banner";
import { IssuesRequiredBanner } from "../github/forked-repository-banner";
import { QuickActions } from "./quick-actions";
import { DraftsSection } from "./drafts-section";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import { UserPopover } from "../user-popover";

import { useThreadsStatus } from "@/hooks/useThreadsStatus";
import { Thread } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { useState, useMemo } from "react";
import { threadsToMetadata } from "@/lib/thread-utils";
import { Settings, BookOpen } from "lucide-react";
import NextLink from "next/link";
import { OpenSWELogoSVG } from "../icons/openswe";

function OpenSettingsButton() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          asChild
          className="hover:bg-accent hover:text-accent-foreground size-6 rounded-md p-1 hover:cursor-pointer"
        >
          <NextLink href="/settings">
            <Settings className="size-4" />
          </NextLink>
        </TooltipTrigger>
        <TooltipContent side="bottom">Open Settings</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const DOCUMENTATION_URL = "https://docs.langchain.com/labs/swe";

function OpenDocumentationButton() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          asChild
          className="hover:bg-accent hover:text-accent-foreground size-6 rounded-md p-1 hover:cursor-pointer"
        >
          <a
            href={DOCUMENTATION_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            <BookOpen className="size-4" />
          </a>
        </TooltipTrigger>
        <TooltipContent side="bottom">Open Documentation</TooltipContent>
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
    dropRef,
    removeBlock,
    dragOver,
    handlePaste,
  } = useFileUpload();
  const [autoAccept, setAutoAccept] = useState(false);

  const threadsMetadata = useMemo(() => threadsToMetadata(threads), [threads]);
  const displayThreads = threadsMetadata.slice(0, 4);
  const displayThreadIds = displayThreads.map((thread) => thread.id);

  const {
    statusMap,
    taskPlanMap,
    isLoading: statusLoading,
  } = useThreadsStatus(displayThreadIds, threads);

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
            <OpenSWELogoSVG
              width={120}
              height={18}
            />
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">ready</span>
              <div className="h-1 w-1 rounded-full bg-green-500 dark:bg-green-600"></div>
            </div>
            <OpenDocumentationButton />
            <OpenSettingsButton />
            <ThemeToggle />
            <UserPopover />
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-6 p-4">
          <GitHubInstallationBanner />
          <ApiKeyBanner />
          <IssuesRequiredBanner />
          {/* Terminal Chat Input */}
          <Card
            className={cn(
              "border-border bg-card py-0",
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
                  <TooltipIconButton
                    variant={autoAccept ? "default" : "ghost"}
                    tooltip="Automatically accept the plan"
                    className={cn(
                      "transition-colors duration-200",
                      autoAccept
                        ? "bg-primary hover:bg-primary/90"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setAutoAccept((prev) => !prev)}
                    side="bottom"
                  >
                    <ListChecks className="size-4" />
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
                    taskPlan={taskPlanMap[thread.id]}
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
