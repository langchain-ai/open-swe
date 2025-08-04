import { PlanViewer } from "./plan-viewer";
import { useProposedPlan } from "../thread/agent-inbox/hooks/useProposedPlan";
import { PlanItem } from "@open-swe/shared/open-swe/types";
import { useStream } from "@langchain/langgraph-sdk/react";
import { X, ArrowRight } from "lucide-react";

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
      <div className="py-8">
        <div className="flex items-center justify-center gap-8">
          <button
            onClick={handleRejectPlan}
            className="group flex cursor-pointer flex-col items-center gap-2 transition-all"
          >
            <div className="rounded-full border-2 border-dashed border-red-500 p-3 transition-all group-hover:border-solid group-hover:bg-red-50 dark:group-hover:bg-red-950/50">
              <X className="h-5 w-5 text-red-500" />
            </div>
            <span className="text-muted-foreground text-xs group-hover:text-red-600 dark:group-hover:text-red-400">
              Reject
            </span>
          </button>

          <button
            onClick={handleResumePlan}
            className="group flex cursor-pointer flex-col items-center gap-2 transition-all"
          >
            <div className="rounded-full border-2 border-dashed border-green-500 p-3 transition-all group-hover:border-solid group-hover:bg-green-50 dark:group-hover:bg-green-950/50">
              <ArrowRight className="h-5 w-5 text-green-500" />
            </div>
            <span className="text-muted-foreground text-xs group-hover:text-green-600 dark:group-hover:text-green-400">
              {changesMade ? "Submit changes" : "Continue"}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
