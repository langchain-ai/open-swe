"use client";

import { DefaultView } from "@/components/v2/default-view";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

export default function ChatPage() {
  const { currentInstallation } = useGitHubAppProvider();
  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    currentInstallation,
  });

  if (!threads) {
    return <div>No threads</div>;
  }

  return (
    <div className="bg-background h-screen">
      <Suspense>
        <Toaster />
        <DefaultView
          threads={threads}
          threadsLoading={threadsLoading}
        />
      </Suspense>
    </div>
  );
}
