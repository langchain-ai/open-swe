"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadViewLoading } from "@/components/v2/thread-view-loading";
import { ThreadErrorCard } from "@/components/v2/thread-error-card";
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
import { THREAD_INITIAL_LOADING_SWR_CONFIG } from "@/lib/swr-config";

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
  const [hasLoadedOnce, setHasLoadedOnce] = React.useState(false);

  const stream = useStream<ManagerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "",
    assistantId: MANAGER_GRAPH_ID,
    threadId: thread_id,
    reconnectOnMount: true,
    fetchStateHistory: false,
  });

  // Aggressive loading for initial load
  const { threads: initialThreads, isLoading: initialLoading } = useThreadsSWR({
    assistantId: hasLoadedOnce ? undefined : MANAGER_GRAPH_ID,
    disableOrgFiltering: true,
    refreshInterval: THREAD_INITIAL_LOADING_SWR_CONFIG.refreshInterval,
    revalidateOnFocus: THREAD_INITIAL_LOADING_SWR_CONFIG.revalidateOnFocus,
    revalidateOnReconnect:
      THREAD_INITIAL_LOADING_SWR_CONFIG.revalidateOnReconnect,
    errorRetryCount: THREAD_INITIAL_LOADING_SWR_CONFIG.errorRetryCount,
    errorRetryInterval: THREAD_INITIAL_LOADING_SWR_CONFIG.errorRetryInterval,
    dedupingInterval: THREAD_INITIAL_LOADING_SWR_CONFIG.dedupingInterval,
  });

  // Normal polling after first load
  const { threads: normalThreads, isLoading: normalLoading } = useThreadsSWR({
    assistantId: hasLoadedOnce ? MANAGER_GRAPH_ID : undefined,
    disableOrgFiltering: true,
  });

  const threads = hasLoadedOnce ? normalThreads : initialThreads;
  const threadsLoading = hasLoadedOnce ? normalLoading : initialLoading;

  React.useEffect(() => {
    if (initialThreads.length > 0 && !hasLoadedOnce) {
      setHasLoadedOnce(true);
    }
  }, [initialThreads.length, hasLoadedOnce]);

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

  const { metadata: currentDisplayThread, statusError } = useThreadMetadata(
    dummyThread as any,
  );

  const handleBackToHome = () => {
    router.push("/chat");
  };

  if (statusError && "message" in statusError && "type" in statusError) {
    return (
      <ThreadErrorCard
        error={statusError}
        onGoBack={handleBackToHome}
      />
    );
  }

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
