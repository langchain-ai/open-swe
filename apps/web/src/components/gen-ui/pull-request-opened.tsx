"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
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
            className="border-gray-200 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-300"
          >
            <Clock className="h-3 w-3" />
            Preparing
          </Badge>
        );
      case "generating":
        return (
          <Badge
            variant="secondary"
            className="border-gray-200 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-300"
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            Opening
          </Badge>
        );
      case "done":
        if (isDraft) {
          return (
            <Badge
              variant="secondary"
              className="border-slate-200 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300"
            >
              <GitPullRequestDraft className="h-3 w-3" />
              Draft
            </Badge>
          );
        }
        return (
          <Badge
            variant="secondary"
            className="border-emerald-200 bg-emerald-100 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-800/50 dark:text-emerald-300"
          >
            <Check className="h-3 w-3" />
            Opened
          </Badge>
        );
    }
  };

  const getStatusText = () => {
    if (status === "done" && prNumber && !isDraft) {
      return `Pull request #${prNumber}`;
    }
    return isDraft
      ? `Draft pull request${prNumber ? ` #${prNumber}` : ""}`
      : `Pull request${prNumber ? ` #${prNumber}` : ""}`;
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
        "group via-background to-background dark:via-background dark:to-background rounded-xl border bg-gradient-to-br from-gray-50/50 transition-shadow dark:from-gray-900/20",
        "shadow-sm hover:shadow-md",
      )}
    >
      {/* Header */}
      <div
        className={cn(
          "relative flex items-center bg-gradient-to-r from-gray-50 to-gray-50/50 p-4 backdrop-blur-sm dark:from-gray-900/20 dark:to-gray-900/10",
          "rounded-xl",
        )}
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-500 shadow-md dark:bg-gray-600">
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
      <AnimatePresence>
        {expanded && description && status === "done" && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="border-t p-4">
              <div className="rounded-lg bg-gray-50/50 p-3 dark:bg-gray-800/30">
                <h4 className="text-muted-foreground mb-2 text-xs font-medium">
                  Description
                </h4>
                <pre className="text-foreground text-sm whitespace-pre-wrap">
                  {description}
                </pre>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
