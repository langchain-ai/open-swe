"use client";

import { useState, useRef, useCallback } from "react";
import {
  HelpCircle,
  Loader2,
  CheckCircle,
  ChevronDown,
  Send,
  Clock,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BasicMarkdownText } from "../thread/markdown-text";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { Textarea } from "../ui/textarea";
import { CustomNodeEvent } from "@open-swe/shared/open-swe/custom-node-events";

type RequestHumanHelpProps = {
  status: "loading" | "generating" | "done";
  helpRequest?: string;
  reasoningText?: string;
  onSubmitResponse?: (response: string) => void;
  requestHelpEvents?: CustomNodeEvent[];
};

export function RequestHumanHelp({
  status,
  helpRequest,
  reasoningText,
  onSubmitResponse,
  requestHelpEvents,
}: RequestHumanHelpProps) {
  const [expanded, setExpanded] = useState(true);
  const [userResponse, setUserResponse] = useState("");
  const [submittedResponse, setSubmittedResponse] = useState<string | null>(
    null,
  );
  const [hasSubmitted, setHasSubmitted] = useState(false);

  // Extract response from requestHelpEvents if available
  const eventResponse = requestHelpEvents?.find(
    (event) =>
      event.data && typeof event.data === "object" && "response" in event.data,
  )?.data?.response as string | undefined;

  const hasEventResponse = !!eventResponse;
  const finalResponse = eventResponse || submittedResponse;
  const isResponded = hasEventResponse || hasSubmitted;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const getStatusBadge = () => {
    if (isResponded) {
      return (
        <Badge
          variant="secondary"
          className="border-emerald-200 bg-emerald-100 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
        >
          <Check className="h-3 w-3" />
          {hasEventResponse ? "Responded" : "Submitted"}
        </Badge>
      );
    }
    switch (status) {
      case "loading":
        return (
          <Badge
            variant="secondary"
            className="border-orange-200 bg-orange-100 text-orange-700 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-300"
          >
            <Clock className="h-3 w-3" />
            Preparing
          </Badge>
        );
      case "generating":
        return (
          <Badge
            variant="secondary"
            className="border-orange-200 bg-orange-100 text-orange-700 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-300"
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            Requesting
          </Badge>
        );
      case "done":
        return (
          <Badge
            variant="secondary"
            className="border-blue-200 bg-blue-100 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
          >
            <HelpCircle className="h-3 w-3" />
            Awaiting Response
          </Badge>
        );
    }
  };

  const getStatusText = () => {
    if (isResponded) {
      return "Human Help Response";
    }
    return "Human Help Requested";
  };

  const getSubtitleText = () => {
    if (hasEventResponse) {
      return "Response received";
    }
    if (hasSubmitted) {
      return "Response submitted successfully";
    }
    switch (status) {
      case "loading":
        return "Preparing help request...";
      case "generating":
        return "Requesting human assistance...";
      case "done":
        return "Awaiting your response";
    }
  };

  const shouldShowToggle = () => {
    return (
      !!helpRequest &&
      (status === "generating" || status === "done" || isResponded)
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

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const renderContent = () => {
    if (!expanded) return null;

    const shouldShowContent =
      status === "done" || status === "generating" || isResponded;

    if (!shouldShowContent) return null;

    return (
      <div className="border-t p-4">
        {helpRequest && (
          <div className="mb-4">
            <div className="bg-muted/30 rounded-lg p-3">
              <h4 className="text-muted-foreground mb-2 text-xs font-medium">
                Help Request
              </h4>
              <div
                id="help-request-description"
                className="text-foreground text-sm"
              >
                <BasicMarkdownText className="text-sm">
                  {helpRequest}
                </BasicMarkdownText>
              </div>
            </div>
          </div>
        )}

        {isResponded && finalResponse ? (
          <div className="bg-muted/30 rounded-lg p-3">
            <h4 className="text-muted-foreground mb-2 text-xs font-medium">
              {hasEventResponse ? "Response" : "Your Response"}
            </h4>
            <div className="rounded-lg border border-emerald-200 bg-emerald-100/50 p-3 dark:border-emerald-800 dark:bg-emerald-900/30">
              <div className="text-sm whitespace-pre-wrap text-emerald-700 dark:text-emerald-300">
                {finalResponse}
              </div>
            </div>
          </div>
        ) : (
          (status === "generating" || status === "done") &&
          onSubmitResponse &&
          !hasEventResponse && (
            <div className="space-y-3">
              <div className="bg-muted/30 rounded-lg p-3">
                <h4 className="text-muted-foreground mb-2 text-xs font-medium">
                  Your Response
                </h4>
                <Textarea
                  ref={textareaRef}
                  value={userResponse}
                  onChange={(e) => setUserResponse(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Type your response here... (Ctrl+Enter to submit)"
                  className="min-h-[80px] text-sm"
                  aria-label="Human help response"
                  aria-describedby="help-request-description"
                />
              </div>
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
    <div
      className={cn(
        "group via-background to-background dark:via-background dark:to-background rounded-xl border bg-gradient-to-br from-orange-50/50 transition-shadow dark:from-orange-950/20",
        !expanded ? "shadow-sm hover:shadow-md" : "",
      )}
    >
      {/* Header */}
      <div
        className={cn(
          "relative flex items-center bg-gradient-to-r from-orange-50 to-orange-50/50 p-4 backdrop-blur-sm dark:from-orange-950/30 dark:to-orange-950/10",
          !expanded ? "rounded-xl" : "rounded-t-xl rounded-b-none",
        )}
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-orange-500 shadow-md dark:bg-orange-600">
          <HelpCircle className="h-4 w-4 text-white" />
        </div>

        <div className="ml-3 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-foreground text-sm font-semibold">
              {getStatusText()}
            </h3>
            {getStatusBadge()}
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            {getSubtitleText()}
          </p>
        </div>

        <div className="flex items-center gap-2">
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
      {renderContent()}

      {/* Reasoning Footer */}
      {reasoningText && status === "done" && expanded && (
        <div className="border-t bg-gradient-to-r from-blue-50 to-blue-50/50 p-3 backdrop-blur-sm dark:from-blue-950/30 dark:to-blue-950/10">
          <div className="flex items-start gap-2">
            <div className="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500 dark:bg-blue-600">
              <HelpCircle className="h-3 w-3 text-white" />
            </div>
            <p className="text-sm text-blue-700 dark:text-blue-300">
              {reasoningText}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
