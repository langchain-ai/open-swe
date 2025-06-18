import { isHumanMessageSDK } from "@/lib/langchain-messages";
import { UseStream, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage } from "../thread/messages/ai";
import { useEffect } from "react";

interface ActionsRendererProps {
  graphId: string;
  threadId: string;
  setProgrammerThreadId?: (threadId: string) => void;
  programmerThreadId?: string;
}

export function ActionsRenderer<State extends Record<string, unknown>>({
  graphId,
  threadId,
  setProgrammerThreadId,
  programmerThreadId,
}: ActionsRendererProps) {
  const stream = useStream<State>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: graphId,
    reconnectOnMount: true,
    threadId,
  });

  const nonHumanMessages = stream.messages?.filter(
    (m) => !isHumanMessageSDK(m),
  );

  // TODO: Need a better way to handle this. Not great like this...
  useEffect(() => {
    if (
      stream.values?.programmerThreadId &&
      typeof stream.values.programmerThreadId === "string" &&
      !programmerThreadId
    ) {
      setProgrammerThreadId?.(stream.values.programmerThreadId as string);
    }
  }, [stream.values]);

  return (
    <div className="flex w-full flex-col gap-2">
      {nonHumanMessages?.map((m) => (
        <AssistantMessage
          key={m.id}
          thread={stream as UseStream<Record<string, unknown>>}
          message={m}
          isLoading={false}
          handleRegenerate={() => {}}
        />
      ))}
    </div>
  );
}
