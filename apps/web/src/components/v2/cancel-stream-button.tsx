import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
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
  const { cancelRun } = useCancelStream({
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
      className="h-13 px-3"
    >
      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
      Stop {streamName}
    </Button>
  );
}
