"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadViewLoading } from "@/components/v2/thread-view-loading";
import { ThreadErrorCard } from "@/components/v2/thread-error-card";
import { useThreadMetadata } from "@/hooks/useThreadMetadata";
import { useThreadsSWR } from "@/hooks/useThreadsSWR";
import { useStream } from "@langchain/langgraph-sdk/react";
import { MANAGER_GRAPH_ID } from "@open-swe/shared/constants";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { useRouter } from "next/navigation";
import * as React from "react";
import { use, useEffect, useRef, useState } from "react";
import { Client, Thread } from "@langchain/langgraph-sdk";

async function fetchInitialThread(
  client: Client<ManagerGraphState>,
  threadId: string,
  reqCount = 0,
): Promise<Thread<ManagerGraphState> | null> {
  try {
    return await client.threads.get(threadId);
  } catch (e) {
    console.error("Failed to fetch thread", {
      requestCount: reqCount,
      error: e,
    });
    // Retry a max of 5 times
    if (reqCount < 5) {
      return fetchInitialThread(client, threadId, reqCount + 1);
    }
    return null;
  }
}

interface ThreadPageProps {
  thread_id: string;
}

export default function ThreadPage({
  params,
}: {
  params: Promise<ThreadPageProps>;
}) {
  const [initialFetchedThread, setInitialFetchedThread] =
    useState<Thread<ManagerGraphState> | null>(null);
  const router = useRouter();
  const { thread_id } = use(params);
  const stream = useStream<ManagerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "",
    assistantId: MANAGER_GRAPH_ID,
    threadId: thread_id,
    reconnectOnMount: true,
    fetchStateHistory: false,
  });

  const { threads, isLoading: threadsLoading } = useThreadsSWR({
    assistantId: MANAGER_GRAPH_ID,
    disableOrgFiltering: true,
  });

  // Find the thread by ID
  const thread = threads.find((t) => t.thread_id === thread_id);

  // We need a thread object for the hook, so use a dummy if not found
  const dummyThread = thread ||
    initialFetchedThread || {
      thread_id,
      values: {},
      status: "idle" as const,
      updated_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
    };

  const { metadata: currentDisplayThread, statusError } = useThreadMetadata(
    dummyThread as any,
  );

  const handleBackToHome = () => {
    router.push("/chat");
  };

  const initialThreadFetched = useRef(false);
  useEffect(() => {
    if (!thread && !initialFetchedThread && !initialThreadFetched.current) {
      fetchInitialThread(stream.client as Client<ManagerGraphState>, thread_id)
        .then(setInitialFetchedThread)
        .finally(() => (initialThreadFetched.current = true));
    }

    if (initialThreadFetched.current && initialFetchedThread && thread) {
      setInitialFetchedThread(null);
    }
  }, [thread_id, thread]);

  if (statusError && "message" in statusError && "type" in statusError) {
    return (
      <ThreadErrorCard
        error={statusError}
        onGoBack={handleBackToHome}
      />
    );
  }

  if (
    (!thread || threadsLoading) &&
    (!initialFetchedThread || !initialThreadFetched.current)
  ) {
    return <ThreadViewLoading onBackToHome={handleBackToHome} />;
  }

  return (
    <div className="bg-background fixed inset-0">
      <ThreadView
        stream={stream}
        displayThread={currentDisplayThread}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
