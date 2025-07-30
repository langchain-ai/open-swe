"use client";

import { useState } from "react";
import {
  GitPullRequest,
  Loader2,
  ChevronDown,
  ExternalLink,
  GitPullRequestDraft,
  Clock,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";

type PullRequestOpenedProps = {
  status: "loading" | "generating" | "done";
  title?: string;
  description?: string;
  url?: string;
  prNumber?: number;
  branch?: string;
  targetBranch?: string;
  isDraft?: boolean;
};

export function PullRequestOpened({
  status,
  title,
  description,
  url,
  prNumber,
  branch,
  targetBranch = "main",
  isDraft = false,
}: PullRequestOpenedProps) {
  const [expanded, setExpanded] = useState(false);

  const getStatusBadge = () => {
    switch (status) {
      case "loading":
        return (
          <Badge
            variant="secondary"
            className="border-purple-200 bg-purple-100 text-purple-700 dark:border-purple-800 dark:bg-purple-950 dark:text-purple-300"
          >
            <Clock className="h-3 w-3" />
            Preparing
          </Badge>
        );
      case "generating":
        return (
          <Badge
            variant="secondary"
            className="border-purple-200 bg-purple-100 text-purple-700 dark:border-purple-800 dark:bg-purple-950 dark:text-purple-300"
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            Opening
          </Badge>
        );
      case "done":
        return (
          <Badge
            variant="secondary"
            className="border-emerald-200 bg-emerald-100 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
          >
            <Check className="h-3 w-3" />
            Opened
          </Badge>
        );
    }
  };

  const getStatusText = () => {
    if (status === "done" && prNumber) {
      return `Pull request #${prNumber}`;
    }
    return isDraft ? "Draft pull request" : "Pull request";
  };

  const getSubtitleText = () => {
    switch (status) {
      case "loading":
        return "Preparing to open pull request...";
      case "generating":
        return "Opening pull request on GitHub...";
      case "done":
        return branch ? `${branch} → ${targetBranch}` : "Successfully opened";
    }
  };

  const shouldShowToggle = () => {
    return status === "done" && description;
  };

  return (
    <div
      className={cn(
        "group via-background to-background dark:via-background dark:to-background rounded-xl border bg-gradient-to-br from-purple-50/50 transition-shadow dark:from-purple-950/20",
        "shadow-sm hover:shadow-md",
      )}
    >
      {/* Header */}
      <div
        className={cn(
          "relative flex items-center bg-gradient-to-r from-purple-50 to-purple-50/50 p-4 backdrop-blur-sm dark:from-purple-950/30 dark:to-purple-950/10",
          "rounded-xl",
        )}
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-purple-500 shadow-md dark:bg-purple-600">
          {isDraft ? (
            <GitPullRequestDraft className="h-4 w-4 text-white" />
          ) : (
            <GitPullRequest className="h-4 w-4 text-white" />
          )}
        </div>

        <div className="ml-3 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-foreground text-sm font-semibold">
              {title || getStatusText()}
            </h3>
            {getStatusBadge()}
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            {getSubtitleText()}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {url && status === "done" && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2"
              asChild
            >
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                title="Open pull request on GitHub"
              >
                <ExternalLink className="h-4 w-4" />
              </a>
            </Button>
          )}
          {shouldShowToggle() && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded(!expanded)}
              className="h-8 px-2"
            >
              <ChevronDown
                className={cn(
                  "h-4 w-4 transition-transform duration-200",
                  !expanded && "-rotate-90",
                )}
              />
            </Button>
          )}
        </div>
      </div>

      {/* Content */}
      {expanded && description && status === "done" && (
        <div className="border-t p-4">
          <div className="rounded-lg bg-muted/30 p-3">
            <h4 className="mb-2 text-xs font-medium text-muted-foreground">
              Description
            </h4>
            <pre className="text-sm whitespace-pre-wrap text-foreground">
              {description}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}


