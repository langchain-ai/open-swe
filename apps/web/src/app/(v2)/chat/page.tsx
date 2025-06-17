"use client";

import { DefaultView } from "@/components/v2/default-view";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreads } from "@/hooks/useThreads";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useRouter } from "next/navigation";

export default function ChatPage() {
  const router = useRouter();
  const { threads } = useThreads<GraphState>();

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] =
    threads?.map(threadToDisplayInfo) ?? [];

  const handleThreadSelect = (thread: ThreadDisplayInfo) => {
    router.push(`/chat/${thread.id}`);
  };

  return (
    <div className="h-screen bg-black">
      <GitHubAppProvider>
        <DefaultView
          threads={displayThreads}
          onThreadSelect={handleThreadSelect}
        />
      </GitHubAppProvider>
    </div>
  );
}
