import { useState } from "react";
import { LoaderCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useStreamContext as useAgentThreadStream } from "@langchain/react";

import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream";
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  useCancelAgentThread,
} from "@/features/agents/lib/queries";
import { cn } from "@/lib/utils";

export interface ActiveRun {
  threadId: string;
  /** Server-reported run state, independent of this client's event stream. */
  running: boolean;
}

export interface ComposerPrimaryActionsProps {
  canSubmit: boolean;
  submitting: boolean;
  onSubmit: () => void;
  /** Enables the stop button for the thread's live run. */
  activeRun?: ActiveRun;
}

function SendIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="14" viewBox="0 0 14 14" width="14">
      <path
        d="M7 11.5V2.5M7 2.5L3 6.5M7 2.5L11 6.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function SendButton({ canSubmit, submitting, onSubmit }: ComposerPrimaryActionsProps) {
  return (
    <button
      aria-label="Send message"
      className={cn(
        "relative isolate flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary/90 text-primary-foreground shadow-xs shadow-primary/25 transition-all duration-150",
        "enabled:cursor-pointer hover:bg-primary hover:scale-105 active:shadow-none",
        "disabled:pointer-events-none disabled:opacity-30 disabled:shadow-none",
      )}
      disabled={!canSubmit}
      onClick={onSubmit}
      type="button"
    >
      {submitting ? <LoaderCircle className="size-3.5 animate-spin" /> : <SendIcon />}
    </button>
  );
}

function StopButton({ disabled, onStop }: { disabled: boolean; onStop: () => void }) {
  return (
    <button
      aria-label="Stop run"
      className={cn(
        "flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full bg-destructive/90 text-white shadow-xs shadow-destructive/25 transition-all duration-150",
        "hover:bg-destructive hover:scale-105 active:shadow-none",
        "disabled:pointer-events-none disabled:opacity-40",
      )}
      disabled={disabled}
      onClick={onStop}
      title="Stop run"
      type="button"
    >
      {disabled ? (
        <LoaderCircle className="size-3.5 animate-spin" />
      ) : (
        <svg aria-hidden="true" fill="currentColor" height="12" viewBox="0 0 12 12" width="12">
          <rect height="8" rx="1.5" width="8" x="2" y="2" />
        </svg>
      )}
    </button>
  );
}

function StreamPrimaryActions(props: ComposerPrimaryActionsProps) {
  const stream = useAgentThreadStream();
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState(false);
  const threadId = props.activeRun?.threadId ?? stream.threadId ?? "";
  const cancelThread = useCancelAgentThread(threadId);

  const handleStop = async () => {
    if (stopping) return;
    setStopping(true);
    try {
      // `stream.stop()` only cancels server-side when this client dispatched the
      // run, so cancel by thread first: a run started from Slack/Linear/GitHub
      // (or joined after a reload) has no client-side run id to cancel.
      if (threadId) {
        try {
          await cancelThread.mutateAsync();
        } catch {
          // Cancellation failed (transient 5xx, or a non-owner viewer). Leave
          // the stream and the thread's status polling untouched: presenting a
          // stopped state here would strand the UI on a still-running run.
          return;
        }
      }
      await stream.disconnect();
      if (threadId) {
        queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
          prev ? { ...prev, status: "interrupted" as const } : prev,
        );
        invalidateAgentThreadLists(queryClient);
      }
    } finally {
      setStopping(false);
    }
  };

  // Server truth (`activeRun.running`) matters as much as the client stream:
  // this browser only sees `isLoading` once it observes a lifecycle event, so a
  // run it never joined would otherwise render an unusable send button.
  if (!stream.isLoading && !props.activeRun?.running) return <SendButton {...props} />;

  return <StopButton disabled={stopping} onStop={() => void handleStop()} />;
}

/** The composer's send button, which becomes a stop button while a run is live. */
export function ComposerPrimaryActions(props: ComposerPrimaryActionsProps) {
  const inAgentThreadStream = useIsInAgentThreadStream();
  if (inAgentThreadStream) return <StreamPrimaryActions {...props} />;
  return <SendButton {...props} />;
}
