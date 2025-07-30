import { UseStream } from "@langchain/langgraph-sdk/react";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
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
  const cancelRun = async () => {
    if (!threadId || !runId) {
      toast.error(`Cannot cancel ${streamName}: Missing thread or run ID`);
      return;
    }

    try {
      await stream.client.runs.cancel(threadId, runId);
      toast.success(`${streamName} cancelled successfully`, {
        description: "The running operation has been stopped",
      });
    } catch (error) {
      const errorStr = String(error);
      const isAbortError = errorStr.toLowerCase().includes("abort");

      if (isAbortError) {
        toast.info(`${streamName} operation cancelled`, {
          description: "The stream was successfully stopped",
        });
      } else {
        console.error(`Error cancelling ${streamName} run:`, error);
        toast.error(`Failed to cancel ${streamName}`, {
          description: errorStr || "Unknown error occurred",
        });
      }
    }
  };

  return { cancelRun };
}
