import { createContext, Dispatch, SetStateAction } from "react";
import { Thread } from "@langchain/langgraph-sdk";
import { GraphState } from "@openswe/shared/open-swe/types";

export interface ThreadContextType {
  threads: Thread<GraphState>[];
  setThreads: Dispatch<SetStateAction<Thread<GraphState>[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
  refreshThreads: () => Promise<void>;
  getThread: (threadId: string) => Promise<Thread<GraphState> | null>;
  recentlyUpdatedThreads: Set<string>;
  handleThreadClick: (
    thread: Thread<GraphState>,
    currentThreadId: string | null,
    setThreadId: (id: string) => void,
  ) => void;
}

export const ThreadContext = createContext<ThreadContextType | undefined>(
  undefined,
);
