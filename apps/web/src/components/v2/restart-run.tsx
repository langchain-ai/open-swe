"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Loader2,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
} from "lucide-react";
import type {
  RestartRunRequest,
  RestartRunResponse,
} from "@/app/api/restart-run/types";
import Link from "next/link";

interface RestartRunProps {
  managerThreadId: string;
  plannerThreadId: string;
  programmerThreadId?: string;
  className?: string;
}

export function RestartRun({
  managerThreadId,
  plannerThreadId,
  programmerThreadId,
  className,
}: RestartRunProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<RestartRunResponse | null>(null);

  const handleRestart = async (): Promise<void> => {
    setIsLoading(true);
    setError(null);
    setResponse(null);

    const body: RestartRunRequest = {
      managerThreadId,
      plannerThreadId,
      ...(programmerThreadId ? { programmerThreadId } : {}),
    };

    try {
      const res = await fetch("/api/restart-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(data.error || "Failed to restart run");
      }
      const data = (await res.json()) as RestartRunResponse;
      setResponse(data);
    } catch (e) {
      const message =
        e instanceof Error ? e.message : "Unexpected error restarting run";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      className={cn(
        "border-border/60 from-background to-background/95 rounded-lg border bg-gradient-to-r p-4",
        className,
      )}
    >
      {response ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="flex size-6 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
              <CheckCircle2 className="size-3 text-green-600 dark:text-green-400" />
            </div>
            <span className="text-foreground text-sm font-medium">
              Run created successfully
            </span>
          </div>

          <Link
            href={`/chat/${response.managerSession.threadId}`}
            className="group bg-primary/10 text-primary hover:bg-primary/20 inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors"
          >
            Open session
            <ExternalLink className="size-3 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="flex size-6 items-center justify-center rounded-full bg-red-100 dark:bg-red-900/30">
              <AlertTriangle className="size-3 text-red-600 dark:text-red-400" />
            </div>
            <span className="text-sm font-medium text-red-700 dark:text-red-300">
              A fatal error occurred
            </span>
          </div>

          <div className="flex items-center gap-3">
            <Button
              onClick={handleRestart}
              disabled={isLoading}
              size="sm"
              variant="outline"
              className="h-7 px-3 text-xs"
            >
              {isLoading ? (
                <>
                  <Loader2 className="mr-1.5 size-3 animate-spin" />
                  Restarting...
                </>
              ) : (
                <>
                  <RotateCcw className="mr-1.5 size-3" />
                  Restart from last checkpoint
                </>
              )}
            </Button>

            {error && <span className="text-destructive text-xs">{error}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
