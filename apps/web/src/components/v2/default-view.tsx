"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Archive,
  BookOpenCheck,
  Eye,
  EyeOff,
  List,
  ListChecks,
} from "lucide-react";
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
import { DEFAULT_CONFIG_KEY, useConfigStore } from "@/hooks/useConfigStore";

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

interface OpenSWEIconProps {
  className?: string;
}

export function OpenSWEIcon({ className }: OpenSWEIconProps) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 489 251"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <path
        d="M365.911 0.21875C433.84 1.08552 488.822 56.802 488.822 125.157L488.812 126.77C487.956 194.386 433.305 249.235 365.911 250.095L364.304 250.104H125.272C57.1425 250.104 1.62615 194.923 0.762695 126.77L0.75293 125.157C0.75293 56.2635 56.606 0.208984 125.272 0.208984H364.304L365.911 0.21875ZM122.818 41.6367C117.969 41.5137 113.355 42.5067 109.691 44.7725C106.076 47.0088 103.335 50.5142 102.191 55.5596C102.188 55.9616 102.116 56.363 101.919 56.75C101.701 57.1764 101.375 57.5021 101.004 57.7744C98.4372 59.6567 96.1759 61.7714 94.2793 64.3252C92.0112 67.5007 90.6805 71.8184 90.6455 76.0713C90.6177 79.4554 91.4103 82.7193 93.1201 85.3115C93.3727 82.9575 93.9713 80.5829 96.0781 78.6719L96.7324 78.0771L97.4023 78.6543C101.794 82.437 108.379 83.7076 113.312 80.9727L114.102 80.5352L114.617 81.2754C120.696 89.9958 122.954 100.352 124.879 110.663C126.815 121.035 128.414 131.339 133.046 140.358C135.498 144.439 137.917 148.511 140.904 152.06L141.511 152.765L141.561 152.82L141.601 152.884C142.204 153.82 143.216 154.851 144.476 155.965C145.731 157.075 147.147 158.195 148.547 159.35C149.926 160.486 151.285 161.652 152.336 162.797C153.361 163.914 154.234 165.156 154.415 166.459L154.424 166.521L154.425 166.582C154.475 170.04 154.304 173.446 154.665 176.775C154.999 179.856 155.782 182.729 157.564 185.239L157.935 185.737L157.997 185.817L158.042 185.909C159.331 188.524 158.34 191.093 156.609 192.817C154.932 194.49 152.396 195.56 150.021 195.307C148.576 195.487 147.095 195.337 145.659 195.075C144.198 194.809 142.672 194.403 141.282 194.084C138.433 193.431 136.045 193.127 134.104 194.171C133.525 194.735 132.841 194.991 132.193 195.112C131.538 195.235 130.823 195.233 130.283 195.251C129.11 195.289 128.487 195.421 128.068 196.127C127.871 196.573 127.545 197 127.277 197.335C127.121 197.53 126.973 197.707 126.837 197.876C128.16 197.041 129.742 196.452 131.708 197.112L132.526 197.388L132.374 198.237C132.31 198.595 132.347 198.79 132.385 198.892C132.42 198.988 132.484 199.079 132.617 199.183C132.763 199.296 132.967 199.405 133.255 199.531C133.483 199.631 133.912 199.801 134.21 199.94L134.806 200.22L134.786 200.877C134.774 201.289 134.703 201.685 134.64 202.018C134.619 202.126 134.598 202.226 134.58 202.321C134.663 202.219 134.749 202.118 134.836 202.019C135.459 201.309 136.264 200.602 137.47 200.288L138.119 200.119L138.522 200.656C139.229 201.599 139.882 202.115 140.477 202.379C141.06 202.638 141.665 202.69 142.346 202.576C143.045 202.459 143.813 202.168 144.688 201.743C145.569 201.316 146.482 200.794 147.516 200.231C149.541 199.128 151.917 197.927 154.683 197.521C157.485 197.11 160.619 197.523 164.16 199.507L163.622 201.378C161.577 201.276 159.906 201.457 158.548 202.425C159.373 202.083 160.116 201.894 160.804 201.83C162.144 201.706 163.158 202.069 163.997 202.49C164.381 202.683 164.796 202.926 165.101 203.091C165.436 203.273 165.73 203.414 166.025 203.508C166.556 203.677 167.182 203.724 168.125 203.345C169.859 202.438 171.741 201.334 173.677 200.569C175.666 199.784 177.826 199.306 180.213 199.684L180.334 201.632C178.537 202.15 177.628 203.1 176.848 204.311C176.429 204.96 176.086 205.619 175.632 206.404C175.213 207.129 174.729 207.898 174.097 208.647C177.619 208.248 179.606 207.436 181.427 206.363C182.466 205.75 183.432 205.066 184.656 204.26C185.857 203.469 187.242 202.604 189.029 201.699C192.125 199.827 195.238 201.238 197.615 202.17C198.896 202.672 199.987 203.063 201.005 203.11C201.947 203.155 202.847 202.904 203.77 202.002L203.776 201.995L203.783 201.989C204.52 201.295 205.371 201.098 206.104 201.034C206.302 201.017 206.501 201.009 206.69 201.005C206.534 200.731 206.35 200.506 206.141 200.319C205.681 199.91 205.048 199.618 204.224 199.427C202.524 199.032 200.379 199.126 198.043 199.139C195.825 199.151 193.392 199.09 191.533 198.182C190.567 197.709 189.737 197.001 189.172 195.966C188.613 194.942 188.353 193.672 188.39 192.133L188.402 191.621L188.825 191.331C191.298 189.642 192.292 187.18 192.695 184.287C192.898 182.833 192.946 181.3 192.965 179.731C192.983 178.184 192.973 176.577 193.067 175.067L193.167 173.483L194.553 174.257C197.708 176.016 201.05 177.407 204.437 178.803C207.594 180.104 210.796 181.41 213.83 183.006L214.435 183.329L214.667 183.456L214.807 183.681C216.394 186.236 218.382 189.131 220.756 191.328C222.952 193.36 225.384 194.707 228.061 194.741C228.158 194.459 228.258 194.18 228.371 193.875L228.66 193.094L229.48 193.237C230.027 193.333 230.627 193.497 231.13 193.621C231.666 193.753 232.135 193.851 232.553 193.874C232.965 193.896 233.255 193.84 233.469 193.727C233.666 193.621 233.897 193.415 234.101 192.946L234.703 191.563L235.742 192.656C238.303 195.35 242.152 195.356 245.982 194.588C246.036 194.516 246.045 194.476 246.046 194.467C246.05 194.437 246.055 194.328 245.925 194.101C245.641 193.604 244.984 192.989 244.039 192.259C243.585 191.908 243.097 191.554 242.599 191.193C242.106 190.836 241.602 190.47 241.137 190.11C240.673 189.751 240.226 189.38 239.852 189.008C239.486 188.644 239.136 188.227 238.922 187.766L238.658 187.196L239.056 186.711C239.08 186.682 239.124 186.623 239.093 186.42C239.057 186.188 238.931 185.852 238.663 185.406C238.129 184.517 237.201 183.455 236.092 182.329C234.992 181.212 233.778 180.094 232.695 179.076C231.671 178.113 230.711 177.19 230.181 176.495C228.17 174.291 227.183 171.641 226.393 169.064C225.58 166.416 224.986 163.914 223.788 161.647L223.77 161.612L223.754 161.575C217.589 147.261 210.56 133.142 200.733 121.114L200.729 121.107L200.723 121.102C195.52 114.516 189.704 108.485 183.75 102.526C177.807 96.5791 171.71 90.6875 166.011 84.4307V84.4297C161.689 79.959 158.965 74.5604 156.423 69.2188C153.856 63.8257 151.483 58.5137 147.85 53.9482L147.825 53.917L147.804 53.8848C142.893 46.6167 132.561 41.884 122.818 41.6367ZM344.5 50.2344C303.369 50.2344 270.025 83.5775 270.025 124.709C270.025 165.84 303.369 199.185 344.5 199.185C385.631 199.185 418.976 165.84 418.976 124.709C418.976 83.5775 385.631 50.2344 344.5 50.2344ZM302.304 95.2109C306.087 91.4277 312.148 91.3096 316.073 94.8564L316.446 95.2109L343.393 122.157L316.446 149.104C312.541 153.008 306.209 153.008 302.304 149.104C298.399 145.198 298.399 138.866 302.304 134.961L315.107 122.157L302.304 109.354L301.949 108.98C298.402 105.055 298.521 98.9942 302.304 95.2109ZM375.989 127.098C381.273 127.366 385.475 131.735 385.475 137.085C385.474 142.435 381.272 146.803 375.989 147.071L375.475 147.085H355.185C349.662 147.085 345.185 142.607 345.185 137.085C345.185 131.562 349.662 127.085 355.185 127.085H375.475L375.989 127.098Z"
        fill="currentColor"
      />
    </svg>
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
  const { getConfig } = useConfigStore();
  const config = getConfig(DEFAULT_CONFIG_KEY);
  const {
    contentBlocks,
    setContentBlocks,
    dropRef,
    removeBlock,
    dragOver,
    handlePaste,
  } = useFileUpload();
  const [autoAccept, setAutoAccept] = useState(false);
  const [shouldCreateIssue, setShouldCreateIssue] = useState(
    config?.shouldCreateIssue != null ? !!config.shouldCreateIssue : true,
  );
  const [langEng, setLangEng] = useState(
    config?.langEng != null ? !!config.langEng : false,
  );

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
                  shouldCreateIssue={shouldCreateIssue}
                  setShouldCreateIssue={setShouldCreateIssue}
                  langEng={langEng}
                  setLangEng={setLangEng}
                />
                <div className="flex items-center gap-2">
                  <TooltipIconButton
                    variant={autoAccept ? "default" : "ghost"}
                    tooltip="Whether or not to automatically accept the plan"
                    className={cn(
                      "transition-colors duration-200",
                      autoAccept
                        ? "bg-primary hover:bg-primary/90"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setAutoAccept((prev) => !prev)}
                    side="bottom"
                  >
                    {autoAccept ? (
                      <ListChecks className="size-4" />
                    ) : (
                      <List className="size-4" />
                    )}
                  </TooltipIconButton>
                  <TooltipIconButton
                    variant={shouldCreateIssue ? "ghost" : "default"}
                    tooltip="Whether or not to create a GitHub issue for the request"
                    className={cn(
                      "transition-colors duration-200",
                      shouldCreateIssue
                        ? "text-muted-foreground hover:text-foreground"
                        : "bg-primary hover:bg-primary/90",
                    )}
                    onClick={() => setShouldCreateIssue((prev) => !prev)}
                    side="bottom"
                  >
                    {shouldCreateIssue ? (
                      <Eye className="size-4" />
                    ) : (
                      <EyeOff className="size-4" />
                    )}
                  </TooltipIconButton>
                  <TooltipIconButton
                    variant={langEng ? "default" : "ghost"}
                    tooltip="Use Langgraph Engineer when writing Langgraph code for better results"
                    className={cn(
                      "transition-colors duration-200",
                      langEng
                        ? "bg-primary hover:bg-primary/90"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setLangEng((prev) => !prev)}
                    side="bottom"
                  >
                    {langEng ? (
                      <OpenSWEIcon className="size-5" />
                    ) : (
                      <OpenSWEIcon className="size-5" />
                    )}
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
