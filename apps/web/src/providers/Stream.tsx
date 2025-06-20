import React, { createContext, useContext, ReactNode } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  uiMessageReducer,
  isUIMessage,
  isRemoveUIMessage,
  type UIMessage,
  type RemoveUIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import { useThreadsContext } from "./Thread";
import { GraphState, GraphUpdate } from "@open-swe/shared/open-swe/types";

const useTypedStream = useStream<
  GraphState,
  {
    UpdateType: GraphUpdate;
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
  githubToken,
}: {
  children: ReactNode;
  apiUrl: string;
  assistantId: string;
  githubToken: string;
}) => {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { refreshThreads } = useThreadsContext();
  const streamValue = useTypedStream({
    apiUrl,
    assistantId,
    reconnectOnMount: true,
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
      sleep().then(() => {
        refreshThreads().catch(console.error);
      });
    },
  });

  return (
    <StreamContext.Provider value={streamValue}>
      {children}
    </StreamContext.Provider>
  );
};

export const StreamProvider: React.FC<{
  children: ReactNode;
  apiUrl: string;
  assistantId: string;
  githubToken: string;
}> = ({ children, apiUrl, assistantId, githubToken }) => {
  return (
    <StreamSession
      apiUrl={apiUrl}
      assistantId={assistantId}
      githubToken={githubToken}
    >
      {children}
    </StreamSession>
  );
};

export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
};

export default StreamContext;
