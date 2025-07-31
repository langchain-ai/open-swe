"use client";

import "../app/globals.css";
import { useState } from "react";
import {
  GitCommit,
  Loader2,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronUp,
  MessageSquare,
  FileText,
} from "lucide-react";
import { BasicMarkdownText } from "../thread/markdown-text";

type PushChangesProps = {
  status: "loading" | "generating" | "done";
  success?: boolean;
  gitStatus?: string;
  commitMessage?: string;
  errorMessage?: string;
  reasoningText?: string;
  summaryText?: string;
};

export function PushChanges({
  status,
  success,
  gitStatus,
  commitMessage,
  errorMessage,
  reasoningText,
  summaryText,
}: PushChangesProps) {
  const [expanded, setExpanded] = useState(
    Boolean(status === "done" && gitStatus),
  );
  const [showReasoning, setShowReasoning] = useState(true);
  const [showSummary, setShowSummary] = useState(true);

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return (
          <div className="h-3.5 w-3.5 rounded-full border border-gray-300" />
        );
      case "generating":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />;
      case "done":
        return success ? (
          <CheckCircle className="h-3.5 w-3.5 text-green-500" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-red-500" />
        );
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing changes...";
      case "generating":
        return "Pushing changes...";
      case "done":
        return success
          ? "Changes pushed successfully"
          : "Failed to push changes";
    }
  };

  return (
    <div className="overflow-hidden rounded-md border border-gray-200">
      {reasoningText && (
        <div className="border-b border-blue-200 bg-blue-50 p-2 dark:border-blue-800/50 dark:bg-blue-950/20">
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex items-center gap-1 text-xs font-normal text-blue-700 hover:text-blue-800 dark:text-blue-300 dark:hover:text-blue-200"
          >
            <MessageSquare className="h-3 w-3" />
            {showReasoning ? "Hide reasoning" : "Show reasoning"}
          </button>
          {showReasoning && (
            <BasicMarkdownText className="mt-1 text-xs font-normal text-blue-800 dark:text-blue-200">
              {reasoningText}
            </BasicMarkdownText>
          )}
        </div>
      )}

      <div className="flex items-center border-b border-gray-200 bg-gray-50 p-2">
        <GitCommit className="mr-2 h-3.5 w-3.5 text-gray-500" />
        <span className="flex-1 text-xs font-normal text-gray-800">
          {getStatusText()}
        </span>
        <div className="flex items-center gap-2">
          {getStatusIcon()}
          {gitStatus && status === "done" && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-gray-500 hover:text-gray-700"
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

      {expanded && gitStatus && status === "done" && (
        <div className="p-2">
          <div className="mb-2">
            <h3 className="mb-1 text-xs font-normal text-gray-500">
              Git Status
            </h3>
            <pre className="rounded border border-gray-200 bg-gray-50 p-1.5 text-xs font-normal whitespace-pre-wrap">
              {gitStatus}
            </pre>
          </div>

          {commitMessage && success && (
            <div>
              <h3 className="mb-1 text-xs font-normal text-gray-500">
                Commit Message
              </h3>
              <div className="rounded border border-gray-200 bg-gray-50 p-1.5 text-xs font-normal">
                {commitMessage}
              </div>
            </div>
          )}

          {errorMessage && !success && (
            <div>
              <h3 className="mb-1 text-xs font-normal text-gray-500">Error</h3>
              <div className="rounded border border-red-100 bg-red-50 p-1.5 text-xs font-normal text-red-500">
                {errorMessage}
              </div>
            </div>
          )}
        </div>
      )}

      {summaryText && status === "done" && (
        <div className="border-t border-green-100 bg-green-50 p-2">
          <button
            onClick={() => setShowSummary(!showSummary)}
            className="flex items-center gap-1 text-xs font-normal text-green-700 hover:text-green-800"
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide summary" : "Show summary"}
          </button>
          {showSummary && (
            <BasicMarkdownText className="mt-1 text-xs text-green-800 dark:text-green-400">
              {summaryText}
            </BasicMarkdownText>
          )}
        </div>
      )}
    </div>
  );
}
