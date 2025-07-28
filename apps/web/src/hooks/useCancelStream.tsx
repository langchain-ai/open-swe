import { UseStream } from "@langchain/langgraph-sdk/react";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useState } from "react";
import { toast } from "sonner";

interface UseCancelStreamProps<State extends PlannerGraphState | GraphState> {
  stream: UseStream<State>;
  threadId?: string;
  runId?: string;
  streamName: "Planner" | "Programmer";
}

export function useCancelStream<State extends PlannerGraphState | GraphState>({
  stream,
  threadId,
  runId,
  streamName,
}: UseCancelStreamProps<State>) {
  const [cancelLoading, setCancelLoading] = useState(false);
  const cancelRun = async () => {
    if (!threadId || !runId) {
      toast.error(`Cannot cancel ${streamName}: Missing thread or run ID`);
      return;
    }

    try {
      setCancelLoading(true);
      await stream.client.runs.cancel(threadId, runId, true);
      toast.success(`${streamName} cancelled successfully`, {
        description: "The running operation has been stopped",
        duration: 5000,
        richColors: true,
      });
    } catch (error) {
      const errorStr = String(error);
      const isAbortError = errorStr.toLowerCase().includes("abort");

      if (isAbortError) {
        toast.info(`${streamName} operation cancelled`, {
          description: "The stream was successfully stopped",
          duration: 5000,
          richColors: true,
        });
      } else {
        console.error(`Error cancelling ${streamName} run:`, error);
        toast.error(`Failed to cancel ${streamName}`, {
          description: errorStr || "Unknown error occurred",
          duration: 5000,
          richColors: true,
        });
      }
    } finally {
      setCancelLoading(false);
    }
  };

  return { cancelRun, cancelLoading };
}
