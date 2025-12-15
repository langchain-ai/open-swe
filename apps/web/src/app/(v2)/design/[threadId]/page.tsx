"use client";

import Link from "next/link";
import { use, useEffect } from "react";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { Button } from "@/components/ui/button";
import { FeatureInsightsPanel } from "@/features/feature-insights/feature-insights-panel";
import { useFeatureGraphStore } from "@/stores/feature-graph-store";

interface DesignPageParams {
  threadId: string;
}

export default function DesignPage({
  params,
}: {
  params: Promise<DesignPageParams>;
}) {
  const { threadId } = use(params);
  const { fetchGraphForThread, clear, isLoading, error } = useFeatureGraphStore(
    useShallow((state) => ({
      fetchGraphForThread: state.fetchGraphForThread,
      clear: state.clear,
      isLoading: state.isLoading,
      error: state.error,
    })),
  );

  useEffect(() => {
    if (threadId) {
      void fetchGraphForThread(threadId, { force: true });
    }

    return () => clear();
  }, [clear, fetchGraphForThread, threadId]);

  return (
    <div className="bg-background min-h-screen">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="sm" className="gap-2">
              <Link href="/chat">
                <ArrowLeft className="size-4" /> Back to chat
              </Link>
            </Button>
            <div className="flex flex-col gap-1">
              <h1 className="text-xl font-semibold">Feature graph</h1>
              <p className="text-muted-foreground text-sm">
                Thread: <span className="font-mono text-xs">{threadId}</span>
              </p>
            </div>
          </div>
          {isLoading && (
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
              <Loader2 className="size-4 animate-spin" />
              Loading graph data
            </div>
          )}
        </div>

        {error ? (
          <div className="border-destructive/50 bg-destructive/5 text-destructive rounded-lg border px-4 py-3 text-sm">
            {error}
          </div>
        ) : null}

        <FeatureInsightsPanel />
      </div>
    </div>
  );
}

