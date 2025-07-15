import { isAIMessageSDK, isHumanMessageSDK } from "@/lib/langchain-messages";
import { UseStream, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage } from "../thread/messages/ai";
import { Dispatch, SetStateAction, useEffect, useRef, useState } from "react";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@open-swe/shared/open-swe/manager/types";
import { useCancelStream } from "@/hooks/useCancelStream";
import {
  isCustomNodeEvent,
  CustomNodeEvent,
  INITIALIZE_NODE_ID,
  ACCEPTED_PLAN_NODE_ID,
  mapCustomEventsToSteps,
} from "@open-swe/shared/open-swe/custom-node-events";
import {
  DO_NOT_RENDER_ID_PREFIX,
  PLANNER_GRAPH_ID,
} from "@open-swe/shared/constants";
import { Message } from "@langchain/langgraph-sdk";
import { InitializeStep } from "../gen-ui/initialize-step";
import { AcceptedPlanStep } from "../gen-ui/accepted-plan-step";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState, PlanItem } from "@open-swe/shared/open-swe/types";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import { LoadingActionsCardContent } from "./thread-view-loading";
import { Interrupt } from "../thread/messages/interrupt";
import { debounce } from "lodash";

interface AcceptedPlanEventData {
  planTitle: string;
  planItems: PlanItem[];
  interruptType: HumanResponse["type"];
}

type AcceptedPlanEvent = CustomNodeEvent & {
  data: AcceptedPlanEventData;
};

function isAcceptedPlanEvent(
  event: CustomNodeEvent,
): event is AcceptedPlanEvent {
  const { data } = event;
  return (
    typeof data === "object" &&
    data !== null &&
    typeof data.planTitle === "string" &&
    Array.isArray(data.planItems) &&
    data.planItems.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        typeof item.index === "number" &&
        typeof item.plan === "string" &&
        typeof item.completed === "boolean",
    ) &&
    (data.interruptType === "accept" || data.interruptType === "edit")
  );
}

function isAcceptedPlanEvents(
  events: CustomNodeEvent[],
): events is AcceptedPlanEvent[] {
  return events.every(isAcceptedPlanEvent);
}

interface ActionsRendererProps {
  graphId: string;
  threadId: string;
  runId?: string;
  setProgrammerSession?: (
    session: ManagerGraphState["programmerSession"],
  ) => void;
  programmerSession?: ManagerGraphState["programmerSession"];
  setSelectedTab?: Dispatch<SetStateAction<"planner" | "programmer">>;
  onStreamReady: (cancelFn: (() => void) | undefined) => void;
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

function addMessagesToState(
  existingMessages: Message[],
  newMessages: Message[],
): Message[] {
  const existingIds = new Set(existingMessages.map((message) => message.id));

  // First deduplicate within newMessages array itself
  const seenNewIds = new Set<string>();
  const uniqueNewMessages = newMessages.filter((message) => {
    // Skip messages without IDs or those already in existingMessages
    if (message.id && existingIds.has(message.id)) return false;

    // Handle duplicates within newMessages
    if (message.id) {
      if (seenNewIds.has(message.id)) return false;
      seenNewIds.add(message.id);
    }

    return true;
  });

  return [...existingMessages, ...uniqueNewMessages];
}

function isNodeEndMessagesUpdate(
  data: unknown,
): data is { output: { messages: Message[] } } {
  return !!(
    typeof data === "object" &&
    data !== null &&
    "output" in data &&
    data.output &&
    typeof data.output === "object" &&
    "messages" in data.output &&
    data.output.messages &&
    Array.isArray(data.output.messages)
  );
}

function isNodeEndCommandUpdate(data: unknown): data is {
  output: { lg_name: string; goto: string; update: ManagerGraphUpdate };
} {
  return !!(
    typeof data === "object" &&
    data !== null &&
    "output" in data &&
    data.output &&
    typeof data.output === "object" &&
    "lg_name" in data.output &&
    "goto" in data.output &&
    "update" in data.output &&
    typeof data.output.lg_name === "string" &&
    (typeof data.output.goto === "string" || Array.isArray(data.output.goto)) &&
    typeof data.output.update === "object"
  );
}

const REVIEWER_NODE_IDS = [
  "initialize-state",
  "generate-review-actions",
  "take-review-actions",
  "diagnose-reviewer-error",
  "final-review",
];

export function ActionsRenderer<State extends PlannerGraphState | GraphState>({
  graphId,
  threadId,
  runId,
  setProgrammerSession,
  programmerSession,
  setSelectedTab,
  onStreamReady,
}: ActionsRendererProps) {
  const [customNodeEvents, setCustomNodeEvents] = useState<CustomNodeEvent[]>(
    [],
  );
  const joinedRunId = useRef<string | undefined>(undefined);
  const [streamLoading, setStreamLoading] = useState(false);
  const [mergedMessages, setMergedMessages] = useState<Message[]>([]);
  const debouncedSetMessages = useRef(
    debounce((messages: Message[]) => {
      setMergedMessages((prev) => addMessagesToState(prev, messages));
    }, 100),
  ).current;

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
    onLangChainEvent: (data) => {
      if (
        data.event === "on_chain_end" &&
        data.metadata?.langgraph_node &&
        REVIEWER_NODE_IDS.includes(data.metadata.langgraph_node as string) &&
        data.data
      ) {
        if (isNodeEndCommandUpdate(data.data)) {
          const outputMessages = data.data.output.update
            .messages as unknown as Message[];
          debouncedSetMessages(outputMessages);
        } else if (isNodeEndMessagesUpdate(data.data)) {
          const outputMessages = data.data.output.messages;
          debouncedSetMessages(outputMessages);
        }
      }
    },
    fetchStateHistory: false,
  });

  const { cancelRun } = useCancelStream<State>({
    stream,
    threadId,
    runId,
    streamName: graphId === "planner" ? "Planner" : "Programmer",
  });

  const initializeEvents = customNodeEvents.filter(
    (e) => e.nodeId === INITIALIZE_NODE_ID,
  );

  const acceptedPlanEvents = customNodeEvents.filter(
    (e) => e.nodeId === ACCEPTED_PLAN_NODE_ID,
  );

  const steps = mapCustomEventsToSteps(initializeEvents);
  const allSuccess =
    steps.length > 0 && steps.every((s) => s.status === "success");

  let initStatus: "loading" | "generating" | "done" = "generating";
  if (allSuccess) {
    initStatus = "done";
  }

  useEffect(() => {
    const allCustomEvents = getCustomNodeEventsFromMessages(mergedMessages);
    if (!allCustomEvents?.length) {
      return;
    }

    setCustomNodeEvents((prev) => {
      // If no existing events, set all new events
      if (prev.length === 0) {
        return allCustomEvents;
      }

      // Merge new events with existing ones, avoiding duplicates
      const existingActionIds = new Set(prev.map((e) => e.actionId));
      const newEvents = allCustomEvents.filter(
        (e) => !existingActionIds.has(e.actionId),
      );

      if (newEvents.length > 0) {
        return [...prev, ...newEvents];
      }

      return prev;
    });
  }, [mergedMessages]);

  // Clear streamLoading as soon as we get any content (agent has started running)
  useEffect(() => {
    const hasContent =
      (mergedMessages && mergedMessages.length > 0) ||
      customNodeEvents.length > 0;

    if (hasContent && streamLoading) {
      setStreamLoading(false);
    }
  }, [mergedMessages, customNodeEvents, streamLoading]);

  // TODO: If the SDK changes go in, use this instead:
  // stream.joinStream(runId, undefined, { streamMode: ["values", "messages", "custom"]}).catch(console.error);
  useEffect(() => {
    if (runId && runId !== joinedRunId.current) {
      joinedRunId.current = runId;
      setStreamLoading(true);
      stream
        .joinStream(runId)
        .catch(console.error)
        .finally(() => setStreamLoading(false));
    } else if (!runId) {
      joinedRunId.current = undefined;
    }
  }, [runId, stream]);

  useEffect(() => {
    if (stream.isLoading) {
      onStreamReady(cancelRun);
    } else {
      onStreamReady(undefined);
    }
  }, [onStreamReady, runId]); // Depend on runId instead of cancelRun to avoid infinite loops

  // Filter out human & do not render messages
  const filteredMessages = mergedMessages?.filter(
    (m) =>
      !isHumanMessageSDK(m) &&
      !(m.id && m.id.startsWith(DO_NOT_RENDER_ID_PREFIX)),
  );
  const isLastMessageHidden = !!(
    mergedMessages?.length > 0 &&
    mergedMessages[mergedMessages.length - 1].id &&
    mergedMessages[mergedMessages.length - 1].id?.startsWith(
      DO_NOT_RENDER_ID_PREFIX,
    )
  );

  // TODO: Need a better way to handle this. Not great like this...
  useEffect(() => {
    if (
      "programmerSession" in stream.values &&
      stream.values.programmerSession &&
      (stream.values.programmerSession.runId !== programmerSession?.runId ||
        stream.values.programmerSession.threadId !==
          programmerSession?.threadId)
    ) {
      setProgrammerSession?.(stream.values.programmerSession);

      // Only switch tabs from the planner ActionsRenderer to ensure proper timing
      // This allows the accepted plan step to be visible before switching
      if (graphId === PLANNER_GRAPH_ID) {
        // Add a small delay to allow the accepted plan step to render first
        setTimeout(() => {
          setSelectedTab?.("programmer");
        }, 2000);
      }
    }
  }, [stream.values, graphId]);

  useEffect(() => {
    debouncedSetMessages(stream.messages);
    return () => {
      debouncedSetMessages.cancel();
    };
  }, [stream.messages, debouncedSetMessages]);

  if (streamLoading) {
    return <LoadingActionsCardContent />;
  }

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
          threadMessages={mergedMessages}
          message={m}
          isLoading={false}
          handleRegenerate={() => {}}
        />
      ))}
      {acceptedPlanEvents.length > 0 &&
        isAcceptedPlanEvents(acceptedPlanEvents) && (
          <AcceptedPlanStep
            planTitle={acceptedPlanEvents[0].data.planTitle}
            planItems={acceptedPlanEvents[0].data.planItems}
            interruptType={acceptedPlanEvents[0].data.interruptType}
          />
        )}
      {/* If the last message is hidden, but there's an interrupt, we must manually render the interrupt */}
      {isLastMessageHidden && stream.interrupt ? (
        <Interrupt
          interruptValue={stream.interrupt?.value}
          isLastMessage={true}
          thread={stream as UseStream<Record<string, unknown>>}
        />
      ) : null}
    </div>
  );
}
