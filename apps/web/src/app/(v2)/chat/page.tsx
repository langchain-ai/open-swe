"use client";

import { DefaultView } from "@/components/v2/default-view";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreads } from "@/hooks/useThreads";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

export default function ChatPage() {
  const { threads, threadsLoading } = useThreads<GraphState>(MANAGER_GRAPH_ID);

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
