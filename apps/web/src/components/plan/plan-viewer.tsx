import { Check, Clock, Pencil, Play, Trash } from "lucide-react";
import { cn } from "@/lib/utils";
import { PlanItem } from "@open-swe/shared/open-swe/types";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import { Dispatch, SetStateAction, useState } from "react";
import { Textarea } from "../ui/textarea";
import { Button } from "../ui/button";

interface PlanViewerProps {
  planItems: PlanItem[];
  className?: string;
  isProposedPlan?: boolean;
  setPlanItems?: Dispatch<SetStateAction<PlanItem[]>>;
}

export function PlanViewer({
  planItems,
  className,
  isProposedPlan,
  setPlanItems,
}: PlanViewerProps) {
  const [isEditing, setIsEditing] = useState<Record<number, boolean>>({});
  const [newPlanItem, setNewPlanItem] = useState("");
  const currentTaskIndex = planItems
    .filter((item) => !item.completed)
    .reduce(
      (minIndex, item) => (item.index < minIndex ? item.index : minIndex),
      Infinity,
    );

  const getTaskStatus = (item: PlanItem) => {
    if (isProposedPlan) return "proposed";
    if (item.completed) return "completed";
    if (item.index === currentTaskIndex) return "current";
    return "remaining";
  };

  return (
    <div className={cn("w-full space-y-3", className)}>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-sm font-medium text-gray-900">
          {isProposedPlan ? "Proposed" : "Execution"} Plan
        </h3>
        {!isProposedPlan && (
          <span className="text-xs text-gray-500">
            {planItems.filter((item) => item.completed).length} of{" "}
            {planItems.length} completed
          </span>
        )}
      </div>

      <div className="space-y-2">
        {planItems
          .sort((a, b) => a.index - b.index)
          .map((item) => {
            const status = getTaskStatus(item);

            return (
              <div
                key={`plan-item-${item.index}`}
                className={cn(
                  "flex items-start gap-3 rounded-lg border p-3 transition-colors",
                  {
                    "border-green-200 bg-green-50": status === "completed",
                    "border-blue-200 bg-blue-50": status === "current",
                    "border-gray-200 bg-gray-50": [
                      "remaining",
                      "proposed",
                    ].includes(status),
                  },
                )}
              >
                {/* Status Icon */}
                <div className="mt-0.5 flex-shrink-0">
                  {status === "completed" && (
                    <Check className="h-4 w-4 text-green-600" />
                  )}
                  {status === "current" && (
                    <Play className="h-4 w-4 text-blue-600" />
                  )}
                  {["remaining", "proposed"].includes(status) && (
                    <Clock className="h-4 w-4 text-gray-400" />
                  )}
                </div>

                {/* Task Content */}
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center justify-between text-gray-500">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium">
                        Step {item.index + 1}
                      </span>
                      {status === "current" && (
                        <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">
                          In Progress
                        </span>
                      )}
                      {status === "completed" && (
                        <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700">
                          Done
                        </span>
                      )}
                      {status === "proposed" && (
                        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                          Proposed
                        </span>
                      )}
                    </div>
                    {isProposedPlan && (
                      <div className="flex items-center gap-2">
                        <TooltipIconButton
                          onClick={() =>
                            setIsEditing((p) => ({
                              ...p,
                              [item.index]: !p[item.index],
                            }))
                          }
                          tooltip={
                            isEditing[item.index] ? `Save Plan` : `Edit Plan`
                          }
                        >
                          {isEditing[item.index] ? (
                            <Check className="size-4" />
                          ) : (
                            <Pencil className="size-4" />
                          )}
                        </TooltipIconButton>
                        <TooltipIconButton
                          onClick={() =>
                            setPlanItems?.((prev) => {
                              const newArr = prev.filter(
                                (i) => i.index !== item.index,
                              );
                              return newArr.map((i, index) => ({
                                ...i,
                                index,
                              }));
                            })
                          }
                          tooltip="Delete Plan"
                          className="transition-colors hover:text-red-500"
                        >
                          <Trash className="size-4" />
                        </TooltipIconButton>
                      </div>
                    )}
                  </div>

                  {isEditing[item.index] ? (
                    <Textarea
                      value={planItems[item.index].plan}
                      onChange={(e) => {
                        setPlanItems?.((prev) => {
                          return prev.map((i) => {
                            if (i.index === item.index) {
                              return {
                                ...i,
                                plan: e.target.value,
                              };
                            }
                            return i;
                          });
                        });
                      }}
                    />
                  ) : (
                    <p
                      className={cn("text-sm leading-relaxed", {
                        "text-gray-900": status === "current",
                        "text-gray-600":
                          status === "completed" ||
                          ["remaining", "proposed"].includes(status),
                      })}
                    >
                      {planItems[item.index].plan}
                    </p>
                  )}

                  {/* Summary for completed tasks */}
                  {item.summary && status === "completed" && (
                    <div className="bg-green-25 mt-2 rounded border border-green-100 p-2">
                      <p className="mb-1 text-xs font-medium text-green-700">
                        Summary:
                      </p>
                      <p className="text-xs text-green-600">{item.summary}</p>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        {isProposedPlan && (
          <div
            key="add-new-plan-item"
            className="flex items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 p-3 transition-colors"
          >
            <Textarea
              placeholder="Add new plan item"
              value={newPlanItem}
              onChange={(e) => setNewPlanItem(e.target.value)}
            />
            <Button
              onClick={() => {
                setPlanItems?.((prev) => [
                  ...prev,
                  {
                    index: prev.length,
                    plan: newPlanItem,
                    completed: false,
                    summary: undefined,
                  },
                ]);
                setNewPlanItem("");
              }}
              className="mt-auto"
              size="sm"
              variant="outline"
            >
              Add
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
