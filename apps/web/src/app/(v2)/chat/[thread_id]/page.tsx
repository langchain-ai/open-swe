"use client";

import { ThreadView } from "@/components/v2/thread-view";
import { ThreadDisplayInfo } from "@/components/v2/types";
import { threadToDisplayInfo } from "@/components/v2/utils/thread-utils";
import { useThreads } from "@/hooks/useThreads";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useRouter } from "next/navigation";
import { notFound } from "next/navigation";

interface ThreadPageProps {
  params: {
    thread_id: string;
  };
}

export default function ThreadPage({ params }: ThreadPageProps) {
  const router = useRouter();
  const { thread_id } = params;
  const { threads } = useThreads<GraphState>();

  // Find the thread by ID
  const thread = threads?.find((t) => t.thread_id === thread_id);

  // If thread not found, show 404
  if (!thread) {
    notFound();
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
        thread={thread}
        displayThread={currentDisplayThread}
        allDisplayThreads={displayThreads}
        onThreadSelect={handleThreadSelect}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
