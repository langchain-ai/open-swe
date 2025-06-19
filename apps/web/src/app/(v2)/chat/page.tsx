"use client";

import { DefaultView } from "@/components/v2/default-view";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreads } from "@/hooks/useThreads";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";

export default function ChatPage() {
  const { threads, threadsLoading } = useThreads<GraphState>(
    process.env.NEXT_PUBLIC_MANAGER_ASSISTANT_ID,
  );

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] =
    threads?.map(threadToDisplayInfo) ?? [];

  return (
    <div className="bg-background h-screen overflow-y-hidden">
      <Suspense>
        <Toaster />
        <GitHubAppProvider>
          <DefaultView threads={displayThreads} threadsLoading={threadsLoading} />
        </GitHubAppProvider>
      </Suspense>
    </div>
  );
}
