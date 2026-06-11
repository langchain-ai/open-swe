import { useMemo, type ReactNode } from "react";
import { HttpAgentServerAdapter, StreamProvider } from "@langchain/react";
import { useQueryClient } from "@tanstack/react-query";

import { AgentThreadStreamBoundary } from "./provider/useIsInAgentThreadStream";
import { agentThreadKeys } from "./queries";
import {
  agentStreamApiUrl,
  agentStreamPaths,
  dashboardFetch,
} from "./streamConfig";

interface AgentThreadStreamProviderProps {
  threadId: string;
  children: ReactNode;
}

export function AgentThreadStreamProvider({
  threadId,
  children,
}: AgentThreadStreamProviderProps) {
  const queryClient = useQueryClient();

  const transport = useMemo(
    () =>
      new HttpAgentServerAdapter({
        apiUrl: agentStreamApiUrl,
        threadId,
        fetch: dashboardFetch,
        paths: agentStreamPaths(threadId),
      }),
    [threadId],
  );

  return (
    <AgentThreadStreamBoundary>
      <StreamProvider
        transport={transport}
        threadId={threadId}
        onCompleted={() => {
          void queryClient.invalidateQueries({ queryKey: agentThreadKeys.detail(threadId) });
          void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true });
        }}
      >
        {children}
      </StreamProvider>
    </AgentThreadStreamBoundary>
  );
}
