"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadViewLoading } from "@/components/v2/thread-view-loading";
import { ThreadMetadata, threadToMetadata } from "@/components/v2/types";
import { useThreadDisplayInfo } from "@/hooks/useThreadDisplayInfo";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { useStream } from "@langchain/langgraph-sdk/react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useRouter } from "next/navigation";
import * as React from "react";
import { use } from "react";

interface ThreadPageProps {
  thread_id: string;
}

export default function ThreadPage({
  params,
}: {
  params: Promise<ThreadPageProps>;
}) {
  const router = useRouter();
  const { thread_id } = use(params);
  const stream = useStream<ManagerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "",
    assistantId: MANAGER_GRAPH_ID,
    threadId: thread_id,
    reconnectOnMount: true,
  });

  const { threads, isLoading: threadsLoading } = useThreadsSWR<GraphState>({
    assistantId: MANAGER_GRAPH_ID,
  });

  // Find the thread by ID
  const thread = threads.find((t) => t.thread_id === thread_id);

  // We need a thread object for the hook, so use a dummy if not found
  const dummyThread = thread || {
    thread_id: thread_id,
    values: {},
    status: "idle" as const,
    updated_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  };

  const { displayInfo: currentDisplayThread } = useThreadDisplayInfo(
    dummyThread as any,
  );

  const handleBackToHome = () => {
    router.push("/chat");
  };

  if (!thread || threadsLoading) {
    return <ThreadViewLoading onBackToHome={handleBackToHome} />;
  }

  // Convert all threads to metadata format
  const threadMetadata: ThreadMetadata[] = threads.map(threadToMetadata);

  return (
    <div className="bg-background fixed inset-0">
      <ThreadView
        stream={stream}
        displayThread={currentDisplayThread}
        allDisplayThreads={threadMetadata}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
