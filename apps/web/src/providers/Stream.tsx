import React, { createContext, useContext, ReactNode, useState } from "react";
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
import {
  CustomNodeEvent,
  isCustomNodeEvent,
} from "@open-swe/shared/open-swe/custom-node-events";

const useTypedStream = useStream<
  GraphState,
  {
    UpdateType: GraphUpdate;
    CustomEventType: UIMessage | RemoveUIMessage;
  }
>;

type StreamContextType = ReturnType<typeof useTypedStream> & {
  customEvents: CustomNodeEvent[];
};
const StreamContext = createContext<StreamContextType | undefined>(undefined);

async function sleep(ms = 4000) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const StreamSession = ({ children }: { children: ReactNode }) => {
  const [threadId, setThreadId] = useQueryState("threadId");
  const [customEvents, setCustomEvents] = useState<CustomNodeEvent[]>([]);
  const { refreshThreads } = useThreadsContext();

  const streamValue = useTypedStream({
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/api",
    assistantId: process.env.NEXT_PUBLIC_MANAGER_ASSISTANT_ID ?? "manager",
    reconnectOnMount: true,
    threadId: threadId ?? null,
    onCustomEvent: (event, options) => {
      if (isCustomNodeEvent(event)) {
        setCustomEvents((prev) => [...prev, event]);
      }
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
    <StreamContext.Provider
      value={{
        ...streamValue,
        customEvents,
      }}
    >
      {children}
    </StreamContext.Provider>
  );
};

export const StreamProvider: React.FC<{
  children: ReactNode;
}> = ({ children }) => {
  return <StreamSession>{children}</StreamSession>;
};

export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
};

export default StreamContext;
