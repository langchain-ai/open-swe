"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadViewLoading } from "@/components/v2/thread-view-loading";
import { ThreadMetadata } from "@/components/v2/types";
import { useThreadMetadata } from "@/hooks/useThreadMetadata";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { useRouter } from "next/navigation";
import * as React from "react";
import { use, useMemo } from "react";
import { threadsToMetadata } from "@/lib/thread-utils";

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
  const { currentInstallation } = useGitHubAppProvider();
  const stream = useStream<ManagerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "",
    assistantId: MANAGER_GRAPH_ID,
    threadId: thread_id,
    reconnectOnMount: true,
    fetchStateHistory: false,
  });

  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    currentInstallation,
  });

  const threadsMetadata = useMemo(() => threadsToMetadata(threads), [threads]);

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

  const { metadata: currentDisplayThread } = useThreadMetadata(
    dummyThread as any,
  );

  const handleBackToHome = () => {
    router.push("/chat");
  };

  if (!thread || threadsLoading) {
    return <ThreadViewLoading onBackToHome={handleBackToHome} />;
  }

  return (
    <div className="bg-background fixed inset-0">
      <ThreadView
        stream={stream}
        displayThread={currentDisplayThread}
        allDisplayThreads={threadsMetadata}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
