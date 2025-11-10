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
        console.log("[ChatPage] Checking provider...");
        const response = await fetch("/api/auth/status");
        const data = await response.json();
        console.log("[ChatPage] Auth status:", data);
        setProvider(data.provider);
        console.log("[ChatPage] Provider set to:", data.provider);
      } catch (error) {
        console.error("[ChatPage] Error checking provider:", error);
      } finally {
        setIsCheckingAuth(false);
        console.log("[ChatPage] Finished checking auth");
      }
    };
    checkProvider();
  }, []);

  console.log("[ChatPage] Rendering - provider:", provider, "isCheckingAuth:", isCheckingAuth, "currentInstallation:", currentInstallation);

  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    currentInstallation: provider === "github" ? currentInstallation : undefined,
    provider: provider,
  });

  console.log("[ChatPage] Threads:", threads, "threadsLoading:", threadsLoading);

  if (isCheckingAuth) {
    console.log("[ChatPage] Showing loading screen");
    return (
      <div className="flex h-screen items-center justify-center">
        <div>Loading...</div>
      </div>
    );
  }

  if (!threads) {
    console.log("[ChatPage] No threads available, provider:", provider);
    return (
      <div className="flex h-screen items-center justify-center">
        <div>No threads available (Provider: {provider || "unknown"})</div>
      </div>
    );
  }

  console.log("[ChatPage] Rendering DefaultView with threads:", threads.length);
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
