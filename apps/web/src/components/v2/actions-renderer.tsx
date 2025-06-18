import { isHumanMessageSDK } from "@/lib/langchain-messages";
import { UseStream, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage } from "../thread/messages/ai";

interface ActionsRendererProps {
  graphId: string;
  threadId: string;
}

export function ActionsRenderer<State extends Record<string, unknown>>({
  graphId,
  threadId,
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
