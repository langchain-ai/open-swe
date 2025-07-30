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

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return (
          <div className="h-3.5 w-3.5 rounded-full border border-gray-300 dark:border-gray-600" />
        );
      case "generating":
        return (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500 dark:text-gray-400" />
        );
      case "done":
        return <CheckCircle className="h-3.5 w-3.5 text-green-500" />;
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing pull request...";
      case "generating":
        return "Opening pull request...";
      case "done":
        return prNumber
          ? `Pull request #${prNumber} opened`
          : "Pull request opened";
    }
  };

  const shouldShowToggle = () => {
    return status === "done" && description;
  };

  const iconClassName = "mr-2 h-3.5 w-3.5";

  const getIconWithTooltip = () => {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>
            {isDraft && (
              <GitPullRequestDraft
                className={cn(
                  iconClassName,
                  "text-gray-500 dark:text-gray-400",
                )}
              />
            )}
            {!isDraft && (
              <GitPullRequest
                className={cn(
                  iconClassName,
                  "text-green-500 dark:text-green-400",
                )}
              />
            )}
          </TooltipTrigger>
          <TooltipContent>
            {isDraft ? "Opened draft pull request" : "Opened pull request"}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  };

  return (
    <div className="overflow-hidden rounded-md border border-gray-200 dark:border-gray-700">
      <div className="flex items-center border-b border-gray-200 bg-gray-50 p-2 dark:border-gray-700 dark:bg-gray-800">
        {getIconWithTooltip()}
        <div className="flex-1">
          {title && status === "done" && (
            <div className="mb-0.5 text-xs font-normal text-gray-800 dark:text-gray-200">
              {title}
            </div>
          )}
          {branch && status === "done" && (
            <div className="text-xs font-normal text-gray-500 dark:text-gray-400">
              {branch} → {targetBranch}
            </div>
          )}
          {!title && (
            <span className="text-xs font-normal text-gray-800 dark:text-gray-200">
              {getStatusText()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-normal text-gray-500 dark:text-gray-400">
            {getStatusText()}
          </span>
          {getStatusIcon()}
          {url && status === "done" && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
              title="Open pull request"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          {shouldShowToggle() && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
            >
              {expanded ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {expanded && description && status === "done" && (
        <div className="border-t border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
          <h3 className="mb-1 text-xs font-normal text-gray-500 dark:text-gray-400">
            Description
          </h3>
          <pre className="text-xs font-normal whitespace-pre-wrap text-gray-800 dark:text-gray-200">
            {description}
          </pre>
        </div>
      )}
    </div>
  );
}

