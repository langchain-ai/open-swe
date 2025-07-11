"use client";

import { DefaultView } from "@/components/v2/default-view";
import { ThreadMetadata, threadToMetadata } from "@/components/v2/types";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

export default function ChatPage() {
  const { threads, isLoading: threadsLoading } = useThreadsSWR<GraphState>({
    assistantId: MANAGER_GRAPH_ID,
  });
  if (!threads) {
    return <div>No threads</div>;
  }

  // Convert Thread objects to ThreadMetadata for UI
  // Real-time status will be handled by individual ThreadCard components
  const threadMetadata: ThreadMetadata[] = threads.map(threadToMetadata);

  return (
    <div className="bg-background h-screen">
      <Suspense>
        <Toaster />
        <GitHubAppProvider>
          <DefaultView
            threads={threadMetadata}
            threadsLoading={threadsLoading}
          />
        </GitHubAppProvider>
      </Suspense>
    </div>
  );
}
