import { isAIMessageSDK, isHumanMessageSDK } from "@/lib/langchain-messages";
import { UseStream, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage } from "../thread/messages/ai";
import { useEffect, useRef, useState } from "react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import {
  isCustomNodeEvent,
  CustomNodeEvent,
  INITIALIZE_NODE_ID,
  mapCustomEventsToSteps,
} from "@open-swe/shared/open-swe/custom-node-events";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import { AIMessage, Message } from "@langchain/langgraph-sdk";
import { InitializeStep } from "../gen-ui/initialize-step";

interface ActionsRendererProps {
  graphId: string;
  threadId: string;
  runId?: string;
  setProgrammerSession?: (
    session: ManagerGraphState["programmerSession"],
  ) => void;
  programmerSession?: ManagerGraphState["programmerSession"];
}

const getCustomNodeEventsFromMessages = (
  messages?: Message[],
  nodeId?: string,
): CustomNodeEvent[] => {
  if (!messages) return [];

  return messages
    .filter((m) => isAIMessageSDK(m))
    .filter((m) => {
      const events = m.additional_kwargs?.customNodeEvents as
        | CustomNodeEvent[]
        | undefined;
      if (!events?.length) return false;
      if (!nodeId) {
        return true;
      }
      return events.some((e) => e.nodeId === nodeId);
    })
    .map((m) => m.additional_kwargs?.customNodeEvents as CustomNodeEvent[])
    .flat();
};

export function ActionsRenderer<State extends Record<string, unknown>>({
  graphId,
  threadId,
  runId,
  setProgrammerSession,
  programmerSession,
}: ActionsRendererProps) {
  const [customNodeEvents, setCustomNodeEvents] = useState<CustomNodeEvent[]>(
    [],
  );
  const stream = useStream<State>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: graphId,
    reconnectOnMount: true,
    threadId,
    onCustomEvent: (event) => {
      if (isCustomNodeEvent(event)) {
        setCustomNodeEvents((prev) => [...prev, event]);
      }
    },
  });

  const initializeEvents = customNodeEvents.filter(
    (e) => e.nodeId === INITIALIZE_NODE_ID,
  );
  const steps = mapCustomEventsToSteps(initializeEvents);
  const allSuccess =
    steps.length > 0 && steps.every((s) => s.status === "success");

  let initStatus: "loading" | "generating" | "done" = "generating";
  if (allSuccess) {
    initStatus = "done";
  }

  useEffect(() => {
    const customInitEvents = getCustomNodeEventsFromMessages(
      stream.messages,
      INITIALIZE_NODE_ID,
    );
    // If there are no custom init events found in messages, or we already have steps from custom events, return
    if (!customInitEvents?.length || initializeEvents.length) {
      return;
    }
    setCustomNodeEvents(customInitEvents);
  }, [stream.messages]);

  const streamJoined = useRef(false);
  useEffect(() => {
    if (!streamJoined.current && runId) {
      streamJoined.current = true;
      stream.joinStream(runId).catch(console.error);
    }
  }, [runId]);

  // Filter out human & do not render messages
  const filteredMessages = stream.messages?.filter(
    (m) =>
      !isHumanMessageSDK(m) &&
      !(m.id && m.id.startsWith(DO_NOT_RENDER_ID_PREFIX)),
  );

  // TODO: Need a better way to handle this. Not great like this...
  useEffect(() => {
    if (
      stream.values?.programmerSession &&
      typeof stream.values.programmerSession === "object" &&
      stream.values.programmerSession &&
      (
        stream.values
          .programmerSession as ManagerGraphState["programmerSession"]
      )?.runId &&
      (
        stream.values
          .programmerSession as ManagerGraphState["programmerSession"]
      )?.threadId &&
      !programmerSession
    ) {
      const programmerSession = stream.values
        .programmerSession as ManagerGraphState["programmerSession"];
      setProgrammerSession?.(programmerSession);
    }
  }, [stream.values]);

  return (
    <div className="flex w-full flex-col gap-2">
      {initializeEvents.length > 0 && steps.length > 0 && (
        <InitializeStep
          status={initStatus}
          steps={steps}
          success={allSuccess}
          collapse={initStatus === "done" && allSuccess}
        />
      )}
      {filteredMessages?.map((m) => (
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
