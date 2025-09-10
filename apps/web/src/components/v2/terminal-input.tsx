"use client";

import type React from "react";
import { v4 as uuidv4 } from "uuid";
import { Dispatch, SetStateAction, useEffect, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { ArrowUp, Loader2 } from "lucide-react";
import { Button } from "../ui/button";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useRouter } from "next/navigation";
import { useQueryState } from "nuqs";
import { GraphState } from "@openswe/shared/open-swe/types";
import { Base64ContentBlock, HumanMessage } from "@langchain/core/messages";
import { toast } from "sonner";
import { DEFAULT_CONFIG_KEY, useConfigStore } from "@/hooks/useConfigStore";
import {
  API_KEY_REQUIRED_MESSAGE,
  MANAGER_GRAPH_ID,
} from "@openswe/shared/constants";
import { ManagerGraphUpdate } from "@openswe/shared/open-swe/manager/types";
import { useDraftStorage } from "@/hooks/useDraftStorage";
import { hasApiKeySet } from "@/lib/api-keys";
import { useUser, DEFAULT_USER } from "@/hooks/useUser";
import { isAllowedUser } from "@/lib/is-allowed-user";

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
  customFramework: boolean;
  setCustomFramework: Dispatch<SetStateAction<boolean>>;
}

const MISSING_API_KEYS_TOAST_CONTENT = (
  <p>
    {API_KEY_REQUIRED_MESSAGE} Please add your API key(s) in{" "}
    <a
      className="text-blue-500 underline underline-offset-1 dark:text-blue-400"
      href="/settings?tab=api-keys"
    >
      settings
    </a>
  </p>
);

const MISSING_API_KEYS_TOAST_OPTIONS = {
  richColors: true,
  duration: 30_000,
  closeButton: true,
};

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
  customFramework,
  setCustomFramework,
}: TerminalInputProps) {
  const { push } = useRouter();
  const { message, setMessage, clearCurrentDraft } = useDraftStorage();
  const { getConfig } = useConfigStore();
  const [selectedRepository] = useQueryState("repo");
  const [loading, setLoading] = useState(false);
  const { user, isLoading: isUserLoading } = useUser();
  const isLocalMode = process.env.NEXT_PUBLIC_OPEN_SWE_LOCAL_MODE === "true";

  const stream = useStream<GraphState>({
    apiUrl,
    assistantId,
    reconnectOnMount: true,
    fetchStateHistory: false,
    defaultHeaders: { "x-local-mode": "true" },
  });

  const handleSend = async () => {
    if (!selectedRepository) {
      toast.error("Please select a repository first", {
        richColors: true,
        closeButton: true,
      });
      return;
    }

    const currentUser = user ?? (isLocalMode ? DEFAULT_USER : null);

    if (!currentUser && !isLocalMode) {
      toast.error("User not found. Please sign in first", {
        richColors: true,
        closeButton: true,
      });
      return;
    }

    const defaultConfig = getConfig(DEFAULT_CONFIG_KEY);

    if (
      currentUser &&
      !isAllowedUser(currentUser.login) &&
      !hasApiKeySet(defaultConfig)
    ) {
      toast.error(
        MISSING_API_KEYS_TOAST_CONTENT,
        MISSING_API_KEYS_TOAST_OPTIONS,
      );
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
          targetRepository: { owner: "", repo: selectedRepository },
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
                ...defaultConfig,
                customFramework,
              },
            },
            ifNotExists: "create",
            streamResumable: true,
            streamMode: ["values", "messages-tuple", "custom"],
          },
        );

        // set session storage so the stream can be resumed after redirect.
        sessionStorage.setItem(`lg:stream:${newThreadId}`, run.run_id);

        // Store the initial message for optimistic rendering
        try {
          const initialMessageData = {
            message: newHumanMessage,
            timestamp: new Date().toISOString(),
          };
          sessionStorage.setItem(
            `lg:initial-message:${newThreadId}`,
            JSON.stringify(initialMessageData),
          );
        } catch (error) {
          // If sessionStorage fails, continue without optimistic rendering
          console.error(
            "Failed to store initial message in sessionStorage:",
            error,
          );
        }

        push(`/chat/${newThreadId}`);
        clearCurrentDraft();
        setMessage("");
        setContentBlocks([]);
        setAutoAcceptPlan(false);
        setCustomFramework(
          defaultConfig?.customFramework != null
            ? !!defaultConfig.customFramework
            : false,
        );
      } catch (e) {
        if (
          typeof e === "object" &&
          e !== null &&
          "message" in e &&
          e.message !== null &&
          typeof e.message === "string" &&
          e.message.includes(API_KEY_REQUIRED_MESSAGE)
        ) {
          toast.error(
            MISSING_API_KEYS_TOAST_CONTENT,
            MISSING_API_KEYS_TOAST_OPTIONS,
          );
        }
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
    <div className="border-border bg-muted hover:border-muted-foreground/50 hover:bg-muted/80 focus-within:border-muted-foreground/70 focus-within:bg-muted/80 focus-within:shadow-muted-foreground/20 rounded-md border p-2 font-mono text-xs transition-all duration-200 focus-within:shadow-md">
      <div className="text-foreground flex items-center gap-1">
        <div className="border-border bg-background/50 flex items-center gap-1 rounded-md border p-1 transition-colors duration-200">
          <span className="text-muted-foreground">agent</span>
          <span className="text-muted-foreground/70">@</span>
          <span className="text-muted-foreground">workspace</span>
        </div>

        {/* Prompt */}
        <span className="text-muted-foreground">$</span>

        <Button
          onClick={handleSend}
          disabled={
            disabled || !message.trim() || !selectedRepository || isUserLoading
          }
          size="icon"
          variant="brand"
          className="ml-auto size-8 rounded-full border border-white/20 transition-all duration-200 hover:border-white/30 disabled:border-transparent"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ArrowUp className="size-4" />
          )}
        </Button>
      </div>

      {/* Multiline Input */}
      <div className="my-2 flex gap-2">
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyPress}
          placeholder={placeholder}
          disabled={disabled}
          className="text-foreground placeholder:text-muted-foreground focus:placeholder:text-muted-foreground/60 max-h-[50vh] min-h-[80px] flex-1 resize-none border-none bg-transparent p-0 font-mono text-xs shadow-none transition-all duration-200 focus-visible:ring-0 focus-visible:ring-offset-0"
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
