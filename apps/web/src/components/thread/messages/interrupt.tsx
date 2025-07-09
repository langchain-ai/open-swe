import { isAgentInboxInterruptSchema } from "@/lib/agent-inbox-interrupt";
import { ThreadView } from "../agent-inbox";
import { GenericInterruptView } from "./generic-interrupt";
import { useStream } from "@langchain/langgraph-sdk/react";

interface InterruptProps {
  interruptValue?: unknown;
  isLastMessage: boolean;
  hasNoAIOrToolMessages?: boolean;
  forceRenderInterrupt?: boolean;
  thread: ReturnType<typeof useStream>;
}

export function Interrupt({
  interruptValue,
  isLastMessage,
  hasNoAIOrToolMessages,
  forceRenderInterrupt,
  thread,
}: InterruptProps) {
  return (
    <>
      {isAgentInboxInterruptSchema(interruptValue) &&
        (isLastMessage || hasNoAIOrToolMessages || forceRenderInterrupt) && (
          <ThreadView
            interrupt={interruptValue}
            thread={thread}
          />
        )}
      {interruptValue &&
      !isAgentInboxInterruptSchema(interruptValue) &&
      (isLastMessage || forceRenderInterrupt) ? (
        <GenericInterruptView interrupt={interruptValue} />
      ) : null}
    </>
  );
}
