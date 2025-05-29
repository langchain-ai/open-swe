import React, { createContext, useContext, ReactNode, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import { type Message } from "@langchain/langgraph-sdk";
import {
  uiMessageReducer,
  isUIMessage,
  isRemoveUIMessage,
  type UIMessage,
  type RemoveUIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import { LangGraphLogoSVG } from "@/components/icons/langgraph";
import { useThreads } from "./Thread";
import { TooltipIconButton } from "@/components/thread/tooltip-icon-button";
import { Copy, CopyCheck } from "lucide-react";
import { motion } from "framer-motion";

type TargetRepository = { owner: string; repo: string };
export type StateType = {
  messages: Message[];
  ui?: UIMessage[];
  targetRepository?: TargetRepository;
};

const useTypedStream = useStream<
  StateType,
  {
    UpdateType: {
      messages?: Message[] | Message | string;
      ui?: (UIMessage | RemoveUIMessage)[] | UIMessage | RemoveUIMessage;
      context?: Record<string, unknown>;
      targetRepository?: TargetRepository;
    };
    CustomEventType: UIMessage | RemoveUIMessage;
  }
>;

type StreamContextType = ReturnType<typeof useTypedStream>;
const StreamContext = createContext<StreamContextType | undefined>(undefined);

async function sleep(ms = 4000) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const StreamSession = ({
  children,
  apiUrl,
  assistantId,
}: {
  children: ReactNode;
  apiUrl: string;
  assistantId: string;
}) => {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { getThreads, setThreads } = useThreads();
  const streamValue = useTypedStream({
    apiUrl,
    assistantId,
    threadId: threadId ?? null,
    onCustomEvent: (event, options) => {
      if (isUIMessage(event) || isRemoveUIMessage(event)) {
        options.mutate((prev) => {
          const ui = uiMessageReducer(prev.ui ?? [], event);
          return { ...prev, ui };
        });
      }
    },
    onThreadId: (id) => {
      setThreadId(id);
      // Refetch threads list when thread ID changes.
      // Wait for some seconds before fetching so we're able to get the new thread that was created.
      sleep().then(() => getThreads().then(setThreads).catch(console.error));
    },
  });

  return (
    <StreamContext.Provider value={streamValue}>
      {children}
    </StreamContext.Provider>
  );
};

export const StreamProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const baseCopyTooltipText = "Copy environment variables";
  const [copyTooltipText, setCopyTooltipText] = useState(baseCopyTooltipText);

  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";

  if (!apiUrl || !assistantId) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex max-w-3xl flex-col rounded-lg border bg-red-50 shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Environment Variables Missing
              </h1>
            </div>
            <p className="text-muted-foreground">
              Whoops, looks like you don&apos;t have an API URL or assistant ID
              set in your environment variables. Please make sure you have both
              of these set before continuing.
            </p>
            <div className="relative">
              <TooltipIconButton
                onClick={() => {
                  const textToCopy = `NEXT_PUBLIC_API_URL=${apiUrl}\nNEXT_PUBLIC_ASSISTANT_ID=${assistantId}`;
                  navigator.clipboard.writeText(textToCopy).then(() => {
                    setCopyTooltipText("Copied!");
                    setTimeout(
                      () => setCopyTooltipText(baseCopyTooltipText),
                      2000,
                    );
                  });
                }}
                className="absolute top-2 right-2 cursor-pointer"
                tooltip={copyTooltipText}
              >
                {copyTooltipText === baseCopyTooltipText ? (
                  <motion.div
                    key="check"
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    transition={{ duration: 0.15 }}
                  >
                    <Copy />
                  </motion.div>
                ) : (
                  <motion.div
                    key="copy"
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    transition={{ duration: 0.15 }}
                  >
                    <CopyCheck className="text-green-500" />
                  </motion.div>
                )}
              </TooltipIconButton>
              <code className="bg-muted flex flex-col gap-2 rounded-md border border-red-200 px-4 py-3 text-sm">
                <span>NEXT_PUBLIC_API_URL={apiUrl}</span>
                <span>NEXT_PUBLIC_ASSISTANT_ID={assistantId}</span>
              </code>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <StreamSession
      apiUrl={apiUrl}
      assistantId={assistantId}
    >
      {children}
    </StreamSession>
  );
};

// Create a custom hook to use the context
export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
};

export default StreamContext;
