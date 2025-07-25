"use client";

import { useState, useRef, useCallback } from "react";
import {
  HelpCircle,
  Loader2,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Send,
} from "lucide-react";
import { BasicMarkdownText } from "../thread/markdown-text";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

type RequestHumanHelpProps = {
  status: "loading" | "generating" | "done";
  helpRequest?: string;
  reasoningText?: string;
  onSubmitResponse?: (response: string) => void;
};

export function RequestHumanHelp({
  status,
  helpRequest,
  reasoningText,
  onSubmitResponse,
}: RequestHumanHelpProps) {
  const [expanded, setExpanded] = useState(true); // Start expanded for help requests
  const [userResponse, setUserResponse] = useState("");
  const [submittedResponse, setSubmittedResponse] = useState<string | null>(null);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="border-border size-3.5 rounded-full border" />;
      case "generating":
        return (
          <Loader2 className="text-muted-foreground size-3.5 animate-spin" />
        );
      case "done":
        return <CheckCircle className="size-3.5 text-green-500" />;
    }
  };

  const getStatusText = () => {
    if (hasSubmitted) {
      return "Response submitted";
    }
    switch (status) {
      case "loading":
        return "Preparing help request...";
      case "generating":
        return "Requesting help...";
      case "done":
        return "Help request sent";
    }
  };

  const shouldShowToggle = () => {
    return (
      !!helpRequest &&
      (status === "generating" || status === "done" || hasSubmitted)
    );
  };

  const handleSubmit = useCallback(() => {
    if (userResponse.trim() && onSubmitResponse) {
      const response = userResponse.trim();
      setSubmittedResponse(response);
      setHasSubmitted(true);
      onSubmitResponse(response);
      setUserResponse("");
    }
  }, [userResponse, onSubmitResponse]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  const renderContent = () => {
    if (!expanded) return null;

    const shouldShowContent =
      status === "done" || status === "generating" || hasSubmitted;

    if (!shouldShowContent) return null;

    return (
      <div className="bg-muted p-3 dark:bg-gray-900">
        {helpRequest && (
          <div className="mb-3">
            <div className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
              Help Request
            </div>
            <div 
              id="help-request-description"
              className="bg-muted-foreground/5 rounded border p-3"
            >
              <BasicMarkdownText className="text-xs">
                {helpRequest}
              </BasicMarkdownText>
            </div>
          </div>
        )}

        {hasSubmitted && submittedResponse ? (
          <div className="space-y-2">
            <div className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
              Your Response
            </div>
            <div className="rounded border border-green-200 bg-green-100/50 p-3 dark:border-green-800 dark:bg-green-900/30">
              <div className="text-xs whitespace-pre-wrap text-green-700 dark:text-green-300">
                {submittedResponse}
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400">
              <CheckCircle className="h-3 w-3" />
              <span>Response submitted successfully</span>
            </div>
          </div>
        ) : (
          (status === "generating" || status === "done") && onSubmitResponse && (
            <div className="space-y-2">
              <Textarea
                ref={textareaRef}
                value={userResponse}
                onChange={(e) => setUserResponse(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type your response here... (Ctrl+Enter to submit)"
                className="min-h-[80px] text-xs"
                aria-label="Human help response"
                aria-describedby="help-request-description"
              />
              <Button
                onClick={handleSubmit}
                disabled={!userResponse.trim()}
                size="sm"
                className="w-full"
              >
                <Send className="mr-2 h-3 w-3" />
                Submit Response
              </Button>
            </div>
          )
        )}
      </div>
    );
  };

  return (
    <div className="border-border overflow-hidden rounded-md border">
      <div className="border-border flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800">
        <HelpCircle className="text-muted-foreground mr-2 size-3.5" />
        <span className="text-foreground/80 flex-1 text-xs font-normal">
          Human Help Requested
        </span>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-xs font-normal">
            {getStatusText()}
          </span>
          {getStatusIcon()}
          {shouldShowToggle() && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-muted-foreground hover:text-foreground"
            >
              {expanded ? (
                <ChevronUp className="size-3.5" />
              ) : (
                <ChevronDown className="size-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {renderContent()}

      {reasoningText && status === "done" && (
        <div className="border-t border-blue-300 bg-blue-100/50 p-2 dark:border-blue-800 dark:bg-blue-900/50">
          <p className="text-xs font-normal text-blue-700 dark:text-blue-300">
            {reasoningText}
          </p>
        </div>
      )}
    </div>
  );
} 