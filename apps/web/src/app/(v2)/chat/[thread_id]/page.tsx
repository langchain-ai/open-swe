"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreads } from "@/hooks/useThreads";
import { useStream } from "@langchain/langgraph-sdk/react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useRouter } from "next/navigation";
import * as React from "react";

interface ThreadPageProps {
  thread_id: string;
}

export default function ThreadPage({
  params,
}: {
  params: Promise<ThreadPageProps>;
}) {
  const router = useRouter();
  const { thread_id } = React.use(params);
  const stream = useStream<ManagerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "",
    assistantId: process.env.NEXT_PUBLIC_MANAGER_ASSISTANT_ID ?? "",
    threadId: thread_id,
    reconnectOnMount: true,
  });

  const { threads } = useThreads<GraphState>();

  // Find the thread by ID
  const thread = threads?.find((t) => t.thread_id === thread_id);
  // If thread not found, show 404
  if (!thread) {
    return <>Loading...</>;
  }

  // Convert all threads to display format
  const displayThreads: ThreadDisplayInfo[] =
    threads?.map(threadToDisplayInfo) ?? [];
  const currentDisplayThread = threadToDisplayInfo(thread);

  const handleThreadSelect = (selectedThread: ThreadDisplayInfo) => {
    router.push(`/chat/${selectedThread.id}`);
  };

  const handleBackToHome = () => {
    router.push("/chat");
  };

  return (
    <div className="h-screen bg-black">
      <ThreadView
        stream={stream}
        displayThread={currentDisplayThread}
        allDisplayThreads={displayThreads}
        onThreadSelect={handleThreadSelect}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
