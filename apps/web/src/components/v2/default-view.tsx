"use client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FilePlus2, Archive } from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadDisplayInfo } from "./types";
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
import { useState } from "react";
import { GitHubLogoutButton } from "../github/github-oauth-button";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

interface DefaultViewProps {
  threads: ThreadDisplayInfo[];
  threadsLoading: boolean;
}

export function DefaultView({ threads, threadsLoading }: DefaultViewProps) {
  const router = useRouter();
  const [quickActionPrompt, setQuickActionPrompt] = useState("");
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
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
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">ready</span>
              <div className="bg-muted h-1 w-1 rounded-full"></div>
            </div>
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
                />
                <div className="flex items-center gap-1">
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
                      <TooltipContent>Attach files</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
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
                {threads.slice(0, 4).map((thread) => (
                  <ThreadCard
                    key={thread.id}
                    thread={thread}
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
        </div>
      </div>
    </div>
  );
}
