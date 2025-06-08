import { isAgentInboxInterruptSchema } from "@/lib/agent-inbox-interrupt";
import { ThreadView } from "../agent-inbox";
import { GenericInterruptView } from "./generic-interrupt";

interface InterruptProps {
  interruptValue?: unknown;
  isLastMessage: boolean;
  hasNoAIOrToolMessages: boolean;
  forceRenderInterrupt?: boolean;
}

export function Interrupt({
  interruptValue,
  isLastMessage,
  hasNoAIOrToolMessages,
  forceRenderInterrupt,
}: InterruptProps) {
  return (
    <>
      {isAgentInboxInterruptSchema(interruptValue) &&
        (isLastMessage || hasNoAIOrToolMessages || forceRenderInterrupt) && (
          <ThreadView interrupt={interruptValue} />
        )}
      {interruptValue &&
      !isAgentInboxInterruptSchema(interruptValue) &&
      (isLastMessage || forceRenderInterrupt) ? (
        <GenericInterruptView interrupt={interruptValue} />
      ) : null}
    </>
  );
}
