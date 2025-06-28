import { UseStream } from "@langchain/langgraph-sdk/react";
import { toast } from "sonner";

interface UseCancelStreamProps {
  stream: UseStream<any>;
  threadId?: string;
  runId?: string;
  streamName: string; // "Planner" | "Programmer"
}

export function useCancelStream({
  stream,
  threadId,
  runId,
  streamName,
}: UseCancelStreamProps) {
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
      const isAbortError =
        error instanceof Error &&
        (error.name === "AbortError" || error.message.includes("abort"));

      if (isAbortError) {
        toast.info(`${streamName} operation cancelled`, {
          description: "The stream was successfully stopped",
        });
      } else {
        console.error(`Error cancelling ${streamName} run:`, error);
        toast.error(`Failed to cancel ${streamName}`, {
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
        });
      }
    }
  };

  return { cancelRun };
}
