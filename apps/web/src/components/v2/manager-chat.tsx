import { ScrollToBottom, StickyToBottomContent } from "@/utils/scroll-utils";
import { Message } from "@langchain/langgraph-sdk";
import { getMessageContentString } from "@open-swe/shared/messages";
import { useState } from "react";
import { StickToBottom } from "use-stick-to-bottom";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import { AnimatePresence, motion } from "framer-motion";
import { Bot, Copy, CopyCheck, Send, User, Loader2 } from "lucide-react";
import { Textarea } from "../ui/textarea";
import { Button } from "../ui/button";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { cn } from "@/lib/utils";
import { isAIMessageSDK } from "@/lib/langchain-messages";
import { BasicMarkdownText } from "../thread/markdown-text";
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
}

function extractResponseFromMessage(message: Message): string {
  if (!isAIMessageSDK(message)) {
    return getMessageContentString(message.content);
  }
  const toolCall = message.tool_calls?.[0];
  const response = toolCall?.args?.response;

  if (!toolCall || !response) {
    return getMessageContentString(message.content);
  }
  return response;
}

export function ManagerChat({
  messages,
  chatInput,
  setChatInput,
  handleSendMessage,
  isLoading,
  cancelRun,
}: ManagerChatProps) {
  return (
    <div className="border-border bg-muted/30 flex h-full w-1/3 flex-col border-r dark:bg-gray-950">
      <div className="relative flex-1">
        <StickToBottom
          className="absolute inset-0"
          initial={true}
        >
          <StickyToBottomContent
            className="h-full overflow-y-auto"
            contentClassName="space-y-4 p-4"
            content={
              <>
                {messages.map((message) => {
                  const messageContentString =
                    extractResponseFromMessage(message);
                  return (
                    <div
                      key={message.id}
                      className="group flex gap-3"
                    >
                      <div className="flex-shrink-0">
                        {message.type === "human" ? (
                          <div className="bg-muted flex h-6 w-6 items-center justify-center rounded-full dark:bg-gray-700">
                            <User className="text-muted-foreground h-3 w-3" />
                          </div>
                        ) : (
                          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 dark:bg-blue-900">
                            <Bot className="h-3 w-3 text-blue-700 dark:text-blue-400" />
                          </div>
                        )}
                      </div>
                      <div className="relative flex-1 space-y-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-muted-foreground text-xs font-medium">
                            {message.type === "human" ? "You" : "Agent"}
                          </span>
                          <div className="opacity-0 transition-opacity group-hover:opacity-100">
                            <MessageCopyButton content={messageContentString} />
                          </div>
                        </div>
                        <BasicMarkdownText className="text-foreground text-sm">
                          {messageContentString}
                        </BasicMarkdownText>
                      </div>
                    </div>
                  );
                })}
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

      <div className="border-border bg-muted/30 border-t p-4 dark:bg-gray-950">
        <div className="flex gap-2">
          <Textarea
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Type your message..."
            className="border-border bg-background text-foreground placeholder:text-muted-foreground min-h-[60px] flex-1 resize-none text-sm dark:bg-gray-900"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !isLoading) {
                e.preventDefault();
                handleSendMessage();
              }
            }}
          />
          <Button
            onClick={isLoading ? cancelRun : handleSendMessage}
            disabled={isLoading ? false : !chatInput.trim()}
            size={isLoading ? "sm" : "icon"}
            variant={isLoading ? "destructive" : "brand"}
            className={cn(isLoading ? "h-12 px-4 py-2" : "")}
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Cancel
              </>
            ) : (
              <Send className="size-4" />
            )}
          </Button>
        </div>
        <div className="text-muted-foreground mt-2 text-xs">
          Press Cmd+Enter to send
        </div>
      </div>
    </div>
  );
}
