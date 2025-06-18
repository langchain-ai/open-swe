import { PlanViewer } from "./plan-viewer";
import { Button } from "../ui/button";
import { useProposedPlan } from "../thread/agent-inbox/hooks/useProposedPlan";
import { PlanItem } from "@open-swe/shared/open-swe/types";
import { useStream } from "@langchain/langgraph-sdk/react";

export function ProposedPlan({
  originalPlanItems,
  stream,
}: {
  originalPlanItems: PlanItem[];
  stream: ReturnType<typeof useStream>;
}) {
  const {
    planItems,
    setPlanItems,
    changesMade,
    handleResumePlan,
    handleRejectPlan,
  } = useProposedPlan(originalPlanItems, stream);

  if (!planItems.length) return null;

  return (
    <div className="my-4 flex flex-col gap-4">
      <PlanViewer
        planItems={planItems}
        setPlanItems={setPlanItems}
        isProposedPlan={true}
      />
      <div className="flex w-full flex-row items-center justify-center gap-2">
        <Button
          onClick={handleResumePlan}
          className="w-full"
          variant="brand"
        >
          {changesMade ? "Submit" : "Approve"}
        </Button>
        <Button
          onClick={handleRejectPlan}
          className="w-full"
          variant="destructive"
        >
          Reject
        </Button>
      </div>
    </div>
  );
}
