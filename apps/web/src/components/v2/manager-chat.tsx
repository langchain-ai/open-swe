import { ScrollToBottom, StickyToBottomContent } from "@/utils/scroll-utils";
import { Message } from "@langchain/langgraph-sdk";
import { getMessageContentString } from "@open-swe/shared/messages";
import { useState } from "react";
import { StickToBottom } from "use-stick-to-bottom";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import { AnimatePresence, motion } from "framer-motion";
import { Bot, Copy, CopyCheck, ArrowUp, User, AlertCircle } from "lucide-react";
import { Textarea } from "../ui/textarea";
import { Button } from "../ui/button";
import { isAIMessageSDK } from "@/lib/langchain-messages";
import { BasicMarkdownText } from "../thread/markdown-text";
import { ErrorState } from "./types";
import { CollapsibleAlert } from "./collapsible-alert";
import { Loader2 } from "lucide-react";
import { parsePartialJson } from "@langchain/core/output_parsers";

function MessageCopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent<HTMLButtonElement, MouseEvent>) => {
    e.stopPropagation();
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <TooltipIconButton
      onClick={(e) => handleCopy(e)}
      variant="ghost"
      tooltip="Copy content"
      className="size-6 p-1"
    >
      <AnimatePresence
        mode="wait"
        initial={false}
      >
        {copied ? (
          <motion.div
            key="check"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.15 }}
          >
            <CopyCheck className="h-3 w-3 text-green-500" />
          </motion.div>
        ) : (
          <motion.div
            key="copy"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.15 }}
          >
            <Copy className="h-3 w-3" />
          </motion.div>
        )}
      </AnimatePresence>
    </TooltipIconButton>
  );
}

interface ManagerChatProps {
  messages: Message[];
  chatInput: string;
  setChatInput: (input: string) => void;
  handleSendMessage: () => void;
  isLoading: boolean;
  cancelRun: () => void;
  errorState?: ErrorState | null;
  githubUser?: {
    login: string;
    avatar_url: string;
    html_url: string;
    name: string | null;
    email: string | null;
  };
}

function extractResponseFromMessage(message: Message): string {
  if (!isAIMessageSDK(message)) {
    return getMessageContentString(message.content);
  }
  if (
    Array.isArray(message.content) &&
    ["input_json_delta", "tool_use"].includes(
      message.content[0].type as string,
    ) &&
    "input" in message.content[0] &&
    message.content[0].input
  ) {
    try {
      const parsedJson = parsePartialJson(message.content[0].input as string);
      if (parsedJson.response) {
        return parsedJson.response;
      }
    } catch {
      // no-op
    }
  }
  const toolCall = message.tool_calls?.[0];
  const response = toolCall?.args?.response;

  if (!toolCall || !response) {
    return getMessageContentString(message.content);
  }
  return response;
}

function LoadingMessageDots() {
  return (
    <div className="text-foreground flex items-center space-x-1 overflow-x-hidden text-sm">
      <style jsx>{`
        @keyframes dotBounce {
          0%,
          80%,
          100% {
            transform: scale(0.8);
            opacity: 0.5;
          }
          40% {
            transform: scale(1.2);
            opacity: 1;
          }
        }
        .dot-bounce {
          animation: dotBounce 1.4s infinite ease-in-out;
        }
      `}</style>
      <div className="flex space-x-1">
        <div
          className="dot-bounce h-1 w-1 rounded-full bg-current"
          style={{
            animationDelay: "0ms",
          }}
        />
        <div
          className="dot-bounce h-1 w-1 rounded-full bg-current"
          style={{
            animationDelay: "200ms",
          }}
        />
        <div
          className="dot-bounce h-1 w-1 rounded-full bg-current"
          style={{
            animationDelay: "400ms",
          }}
        />
      </div>
    </div>
  );
}

export function ManagerChat({
  messages,
  chatInput,
  setChatInput,
  handleSendMessage,
  isLoading,
  cancelRun,
  errorState,
  githubUser,
}: ManagerChatProps) {
  return (
    <div className="border-border bg-muted/30 flex h-full w-1/3 flex-col overflow-hidden border-r">
      <div className="relative flex-1">
        <StickToBottom
          className="absolute inset-0"
          initial={true}
        >
          <StickyToBottomContent
            className="scrollbar-pretty-auto h-full"
            contentClassName="space-y-4 p-4"
            content={
              <>
                {messages.map((message) => {
                  const messageContentString =
                    extractResponseFromMessage(message);
                  return (
                    <div
                      key={message.id}
                      className="group bg-muted flex items-start gap-3 rounded-lg p-3"
                    >
                      <div className="mt-0.5 flex-shrink-0">
                        {message.type === "human" ? (
                          githubUser?.avatar_url ? (
                            <div className="bg-muted flex h-6 w-6 items-center justify-center overflow-hidden rounded-full">
                              <img
                                src={githubUser.avatar_url}
                                alt={githubUser.login}
                                className="h-full w-full object-cover"
                              />
                            </div>
                          ) : (
                            <div className="bg-muted flex h-6 w-6 items-center justify-center rounded-full">
                              <User className="text-muted-foreground h-4 w-4" />
                            </div>
                          )
                        ) : (
                          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 dark:bg-blue-950/50">
                            <Bot className="size-4.5 text-blue-700 dark:text-blue-300" />
                          </div>
                        )}
                      </div>
                      <div className="relative min-w-0 flex-1 space-y-1 overflow-x-hidden">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-muted-foreground text-xs font-medium">
                            {message.type === "human"
                              ? githubUser?.login || "You"
                              : "Open SWE"}
                          </span>
                          <div className="opacity-0 transition-opacity group-hover:opacity-100">
                            <MessageCopyButton content={messageContentString} />
                          </div>
                        </div>
                        {messageContentString ? (
                          <BasicMarkdownText className="text-foreground overflow-x-hidden text-sm">
                            {messageContentString}
                          </BasicMarkdownText>
                        ) : (
                          <LoadingMessageDots />
                        )}
                      </div>
                    </div>
                  );
                })}
                {errorState ? (
                  <CollapsibleAlert
                    variant="destructive"
                    errorState={errorState}
                    icon={<AlertCircle className="size-4" />}
                  />
                ) : null}
              </>
            }
            footer={
              <div className="absolute right-0 bottom-4 left-0 flex w-full justify-center">
                <ScrollToBottom className="animate-in fade-in-0 zoom-in-95" />
              </div>
            }
          />
        </StickToBottom>
      </div>

      <div className="border-border bg-muted/30 border-t p-4">
        <div className="flex gap-2">
          <Textarea
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Type your message..."
            className="border-border bg-background text-foreground placeholder:text-muted-foreground min-h-[60px] flex-1 resize-none text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !isLoading) {
                e.preventDefault();
                handleSendMessage();
              }
            }}
          />
          {isLoading ? (
            <TooltipIconButton
              className="size-8 rounded-full border border-white/20 transition-all duration-200 hover:border-white/30 disabled:border-transparent"
              variant="destructive"
              onClick={cancelRun}
              tooltip="Cancel run"
            >
              <Loader2 className="size-4 animate-spin" />
            </TooltipIconButton>
          ) : (
            <Button
              onClick={handleSendMessage}
              disabled={!chatInput.trim()}
              size="icon"
              variant="brand"
              className="size-8 rounded-full border border-white/20 transition-all duration-200 hover:border-white/30 disabled:border-transparent"
            >
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
        <div className="text-muted-foreground mt-2 text-xs">
          Press Cmd+Enter to send
        </div>
      </div>
    </div>
  );
}
