import { isHumanMessageSDK } from "@/lib/langchain-messages";
import { UseStream, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage } from "../thread/messages/ai";
import { useEffect, useRef } from "react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";

interface ActionsRendererProps {
  graphId: string;
  threadId: string;
  runId?: string;
  setProgrammerSession?: (session: ManagerGraphState["programmerSession"]) => void;
  programmerSession?: ManagerGraphState["programmerSession"];
}

export function ActionsRenderer<State extends Record<string, unknown>>({
  graphId,
  threadId,
  runId,
  setProgrammerSession,
  programmerSession,
}: ActionsRendererProps) {
  const stream = useStream<State>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: graphId,
    reconnectOnMount: true,
    threadId,
  });

  const streamJoined = useRef(false);
  useEffect(() => {
    if (!streamJoined.current && runId) {
      streamJoined.current = true;
      stream.joinStream(runId).catch(console.error);
    }
  }, [runId])

  const nonHumanMessages = stream.messages?.filter(
    (m) => !isHumanMessageSDK(m),
  );

  // TODO: Need a better way to handle this. Not great like this...
  useEffect(() => {
    if (
      stream.values?.programmerSession &&
      typeof stream.values.programmerSession === "object" &&
      stream.values.programmerSession &&
      (stream.values.programmerSession as ManagerGraphState["programmerSession"])?.runId &&
      (stream.values.programmerSession as ManagerGraphState["programmerSession"])?.threadId &&
      !programmerSession
    ) {
      const programmerSession = stream.values.programmerSession as ManagerGraphState["programmerSession"];
      setProgrammerSession?.(programmerSession);
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
