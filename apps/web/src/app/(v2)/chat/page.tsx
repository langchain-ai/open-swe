"use client";

import { DefaultView } from "@/components/v2/default-view";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

export default function ChatPage() {
  const { threads, isLoading: threadsLoading } = useThreadsSWR<GraphState>({
    assistantId: MANAGER_GRAPH_ID,
    refreshInterval: 15000, // Poll every 15 seconds
  });
  if (!threads) {
    return <div>No threads</div>;
  }

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] = threads.map(threadToDisplayInfo);

  return (
    <div className="bg-background h-screen">
      <Suspense>
        <Toaster />
        <GitHubAppProvider>
          <DefaultView
            threads={displayThreads}
            threadsLoading={threadsLoading}
          />
        </GitHubAppProvider>
      </Suspense>
    </div>
  );
}
