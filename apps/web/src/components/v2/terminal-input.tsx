"use client";

import type React from "react";
import { v4 as uuidv4 } from "uuid";
import { Dispatch, SetStateAction, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, Send } from "lucide-react";
import { RepositoryBranchSelectors } from "../github/repo-branch-selectors";
import { Button } from "../ui/button";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useRouter } from "next/navigation";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { Message } from "@langchain/langgraph-sdk";
import { useFileUpload } from "@/hooks/useFileUpload";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Base64ContentBlock, HumanMessage } from "@langchain/core/messages";
import { toast } from "sonner";
import { DEFAULT_CONFIG_KEY, useConfigStore } from "@/hooks/useConfigStore";

interface TerminalInputProps {
  placeholder?: string;
  disabled?: boolean;
  apiUrl: string;
  assistantId: string;
  contentBlocks: Base64ContentBlock[];
  setContentBlocks: Dispatch<SetStateAction<Base64ContentBlock[]>>;
  onPaste?: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
}

export function TerminalInput({
  placeholder = "Enter your command...",
  disabled = false,
  apiUrl,
  assistantId,
  contentBlocks,
  setContentBlocks,
  onPaste,
}: TerminalInputProps) {
  const { push } = useRouter();
  const [message, setMessage] = useState("");
  const { getConfig } = useConfigStore();
  const { selectedRepository } = useGitHubAppProvider();
  const [loading, setLoading] = useState(false);

  const stream = useStream<GraphState>({
    apiUrl,
    assistantId,
    reconnectOnMount: true,
    threadId: null,
    onThreadId: (id) => {
      push(`/chat/${id}`);
      setLoading(false);
      setMessage("");
      setContentBlocks([]);
    },
  });

  const handleSend = () => {
    if (!selectedRepository) {
      toast.error("Please select a repository first", {
        richColors: true,
        closeButton: true,
      });
      return;
    }
    setLoading(true);
    const trimmedMessage = message.trim();
    if (trimmedMessage.length > 0 || contentBlocks.length > 0) {
      const newHumanMessage = new HumanMessage({
        id: uuidv4(),
        content: [
          ...(trimmedMessage.length > 0
            ? [{ type: "text", text: trimmedMessage }]
            : []),
          ...contentBlocks,
        ],
      });

      stream.submit(
        {
          messages: [newHumanMessage],
          targetRepository: selectedRepository,
        },
        {
          streamMode: ["values"],
          optimisticValues: (prev) => ({
            ...prev,
            messages: [...(prev.messages ?? []), newHumanMessage],
          }),
          config: {
            recursion_limit: 400,
            configurable: {
              ...getConfig(DEFAULT_CONFIG_KEY),
            },
          },
        },
      );
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-border bg-muted rounded-md border p-2 font-mono text-xs dark:bg-black">
      <div className="text-foreground flex items-start gap-1">
        {/* User@Host */}
        <span className="text-muted-foreground">open-swe</span>
        <span className="text-muted-foreground/70">@</span>
        <span className="text-muted-foreground">github</span>
        <span className="text-muted-foreground/70">:</span>

        {/* Repository & Branch Selectors */}
        <RepositoryBranchSelectors />

        {/* Prompt */}
        <span className="text-muted-foreground">$</span>
      </div>

      {/* Multiline Input */}
      <div className="mt-1 flex gap-2">
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyPress}
          placeholder={placeholder}
          disabled={disabled}
          className="text-foreground placeholder:text-muted-foreground min-h-[40px] flex-1 resize-none border-none bg-transparent p-0 font-mono text-xs focus-visible:ring-0 focus-visible:ring-offset-0"
          rows={3}
          onPaste={onPaste}
        />
        <Button
          onClick={handleSend}
          disabled={disabled || !message.trim()}
          size="sm"
          className="bg-muted-foreground/20 hover:bg-muted-foreground/30 h-7 w-7 self-end p-0 dark:bg-gray-700 hover:dark:bg-gray-600"
        >
          {loading ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Send className="size-3" />
          )}
        </Button>
      </div>

      {/* Help text */}
      <div className="text-muted-foreground mt-1 text-xs">
        Press Cmd+Enter to send
      </div>
    </div>
  );
}
