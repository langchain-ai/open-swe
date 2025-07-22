"use client";

import { DefaultView } from "@/components/v2/default-view";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { GitHubAppProvider, useGitHubAppProvider } from "@/providers/GitHubApp";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

function ChatPageContent() {
  const { currentInstallation } = useGitHubAppProvider();
  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    currentInstallation,
  });

  if (!threads) {
    return <div>No threads</div>;
  }

  return (
    <DefaultView
      threads={threads}
      threadsLoading={threadsLoading}
    />
  );
}

export default function ChatPage() {
  return (
    <div className="bg-background h-screen">
      <Suspense>
        <Toaster />
        <GitHubAppProvider>
          <ChatPageContent />
        </GitHubAppProvider>
      </Suspense>
    </div>
  );
}
