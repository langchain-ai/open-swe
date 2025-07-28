import { Button } from "@/components/ui/button";
import { Loader2, SquareStop } from "lucide-react";
import { UseStream } from "@langchain/langgraph-sdk/react";
import { useCancelStream } from "@/hooks/useCancelStream";

interface CancelStreamButtonProps {
  stream: UseStream<any>;
  threadId?: string;
  runId?: string;
  streamName: "Planner" | "Programmer";
}

export function CancelStreamButton({
  stream,
  threadId,
  runId,
  streamName,
}: CancelStreamButtonProps) {
  const { cancelRun, cancelLoading } = useCancelStream({
    stream,
    threadId,
    runId,
    streamName,
  });

  const shouldShow = stream.isLoading && threadId && runId;

  if (!shouldShow) {
    return null;
  }

  return (
    <Button
      onClick={cancelRun}
      size="sm"
      variant="destructive"
      className="h-8 px-3 text-xs"
      disabled={cancelLoading}
    >
      {cancelLoading ? (
        <>
          <SquareStop className="mr-1 h-3 w-3 animate-pulse" />
          <p className="animate-pulse">Stopping {streamName}</p>
        </>
      ) : (
        <>
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          Stop {streamName}
        </>
      )}
    </Button>
  );
}
