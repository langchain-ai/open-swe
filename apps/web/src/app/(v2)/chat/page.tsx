"use client";

import { DefaultView } from "@/components/v2/default-view";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { Toaster } from "@/components/ui/sonner";
import { Suspense, useState, useEffect } from "react";
import { MANAGER_GRAPH_ID } from "@openswe/shared/constants";

function ChatPageComponent() {
  const { currentInstallation } = useGitHubAppProvider();
  const [provider, setProvider] = useState<"github" | "gitlab" | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    const checkProvider = async () => {
      try {
        const response = await fetch("/api/auth/status");
        const data = await response.json();
        setProvider(data.provider);
      } catch (error) {
        console.error("[ChatPage] Error checking provider:", error);
      } finally {
        setIsCheckingAuth(false);
      }
    };
    checkProvider();
  }, []);

  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    currentInstallation: provider === "github" ? currentInstallation : undefined,
    provider: provider,
  });

  if (isCheckingAuth) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div>Loading...</div>
      </div>
    );
  }

  if (!threads) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div>No threads available (Provider: {provider || "unknown"})</div>
      </div>
    );
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

export default function ChatPage() {
  return <ChatPageComponent />;
}
