import { StateView } from "./components/state-view";
import { ThreadActionsView } from "./components/thread-actions-view";
import { useState } from "react";
import { HumanInterrupt } from "@langchain/langgraph/prebuilt";
import { parsePlanData } from "@/lib/plan-utils";
import { ProposedPlan } from "@/components/plan/proposed-plan";
import { useStream } from "@langchain/langgraph-sdk/react";

interface ThreadViewProps {
  interrupt: HumanInterrupt | HumanInterrupt[];
  thread: ReturnType<typeof useStream>;
}

export function ThreadView({ interrupt, thread }: ThreadViewProps) {
  const interruptObj = Array.isArray(interrupt) ? interrupt[0] : interrupt;
  const [showDescription, setShowDescription] = useState(false);
  const [showState, setShowState] = useState(false);
  const showSidePanel = showDescription || showState;

  const handleShowSidePanel = (
    showState: boolean,
    showDescription: boolean,
  ) => {
    if (showState && showDescription) {
      console.error("Cannot show both state and description");
      return;
    }
    if (showState) {
      setShowDescription(false);
      setShowState(true);
    } else if (showDescription) {
      setShowState(false);
      setShowDescription(true);
    } else {
      setShowState(false);
      setShowDescription(false);
    }
  };

  const planItems = parsePlanData(interruptObj.action_request.args);
  if (planItems?.length) {
    return (
      <ProposedPlan
        originalPlanItems={planItems}
        stream={thread}
      />
    );
  }

  return (
    <div className="flex h-[80vh] w-full flex-col overflow-y-scroll rounded-2xl bg-gray-50/50 p-8 lg:flex-row [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {showSidePanel ? (
        <StateView
          handleShowSidePanel={handleShowSidePanel}
          description={interruptObj.description}
          values={thread.values}
          view={showState ? "state" : "description"}
        />
      ) : (
        <ThreadActionsView
          interrupt={interruptObj}
          handleShowSidePanel={handleShowSidePanel}
          showState={showState}
          showDescription={showDescription}
          stream={thread}
        />
      )}
    </div>
  );
}
