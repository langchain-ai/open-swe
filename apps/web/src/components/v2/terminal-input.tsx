"use client";

import type React from "react";
import { v4 as uuidv4 } from "uuid";
import { Dispatch, SetStateAction, useEffect, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, Send } from "lucide-react";
import { RepositoryBranchSelectors } from "../github/repo-branch-selectors";
import { Button } from "../ui/button";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useRouter } from "next/navigation";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Base64ContentBlock, HumanMessage } from "@langchain/core/messages";
import { toast } from "sonner";
import { DEFAULT_CONFIG_KEY, useConfigStore } from "@/hooks/useConfigStore";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";
import { useDraftStorage } from "@/hooks/useDraftStorage";

interface TerminalInputProps {
  placeholder?: string;
  disabled?: boolean;
  apiUrl: string;
  assistantId: string;
  contentBlocks: Base64ContentBlock[];
  setContentBlocks: Dispatch<SetStateAction<Base64ContentBlock[]>>;
  onPaste?: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  quickActionPrompt?: string;
  setQuickActionPrompt?: Dispatch<SetStateAction<string>>;
  autoAcceptPlan: boolean;
  setAutoAcceptPlan: Dispatch<SetStateAction<boolean>>;
  draftToLoad?: string;
}

export function TerminalInput({
  placeholder = "Enter your command...",
  disabled = false,
  apiUrl,
  assistantId,
  contentBlocks,
  setContentBlocks,
  onPaste,
  quickActionPrompt,
  setQuickActionPrompt,
  autoAcceptPlan,
  setAutoAcceptPlan,
  draftToLoad,
}: TerminalInputProps) {
  const { push } = useRouter();
  const { message, setMessage, clearCurrentDraft } = useDraftStorage();
  const { getConfig } = useConfigStore();
  const { selectedRepository } = useGitHubAppProvider();
  const [loading, setLoading] = useState(false);

  const stream = useStream<GraphState>({
    apiUrl,
    assistantId,
    reconnectOnMount: true,
    fetchStateHistory: false,
  });

  const handleSend = async () => {
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

      try {
        const newThreadId = uuidv4();
        const runInput: ManagerGraphUpdate = {
          messages: [newHumanMessage],
          targetRepository: selectedRepository,
          autoAcceptPlan,
        };
        const run = await stream.client.runs.create(
          newThreadId,
          MANAGER_GRAPH_ID,
          {
            input: runInput,
            config: {
              recursion_limit: 400,
              configurable: {
                ...getConfig(DEFAULT_CONFIG_KEY),
              },
            },
            ifNotExists: "create",
            streamResumable: true,
            streamMode: ["values", "messages-tuple", "custom"],
          },
        );

        // set session storage so the stream can be resumed after redirect.
        sessionStorage.setItem(`lg:stream:${newThreadId}`, run.run_id);
        push(`/chat/${newThreadId}`);
        clearCurrentDraft();
        setMessage("");
        setContentBlocks([]);
        setAutoAcceptPlan(false);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    if (quickActionPrompt && message !== quickActionPrompt) {
      setMessage(quickActionPrompt);
      // Clear quick action prompt
      setQuickActionPrompt?.("");
    }
  }, [quickActionPrompt]);

  // Handle draft loading from external components
  useEffect(() => {
    if (draftToLoad) {
      setMessage(draftToLoad);
    }
  }, [draftToLoad, setMessage]);

  return (
    <div className="border-border bg-muted rounded-md border p-2 font-mono text-xs dark:bg-black">
      <div className="text-foreground flex items-center gap-1">
        <div className="flex items-center gap-1 rounded-md border border-gray-200 p-1 dark:border-gray-700">
          <span className="text-muted-foreground">open-swe</span>
          <span className="text-muted-foreground/70">@</span>
          <span className="text-muted-foreground">github</span>
        </div>

        {/* Repository & Branch Selectors */}
        <RepositoryBranchSelectors />

        {/* Prompt */}
        <span className="text-muted-foreground">$</span>

        <Button
          onClick={handleSend}
          disabled={disabled || !message.trim() || !selectedRepository}
          size="icon"
          variant="brand"
          className="ml-auto size-8"
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
        </Button>
      </div>

      {/* Multiline Input */}
      <div className="mt-2 flex gap-2">
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyPress}
          placeholder={placeholder}
          disabled={disabled}
          className="text-foreground placeholder:text-muted-foreground min-h-[80px] flex-1 resize-none border-none bg-transparent p-0 font-mono text-xs shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
          rows={6}
          onPaste={onPaste}
        />
      </div>

      {/* Help text */}
      <div className="text-muted-foreground mt-1 text-xs">
        Press Cmd+Enter to send
      </div>
    </div>
  );
}
