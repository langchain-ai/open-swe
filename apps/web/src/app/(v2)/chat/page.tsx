"use client";

import { DefaultView } from "@/components/v2/default-view";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { Toaster } from "@/components/ui/sonner";
import { Suspense } from "react";
import { MANAGER_GRAPH_ID } from "@openswe/shared/constants";

function ChatPageComponent() {
  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
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

export default function ChatPage() {
  return <ChatPageComponent />;
}
