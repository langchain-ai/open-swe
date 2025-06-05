import { Check, Clock, Play } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PlanItem {
  index: number;
  plan: string;
  completed: boolean;
  summary?: string;
}

interface PlanViewerProps {
  planItems: PlanItem[];
  className?: string;
}

export function PlanViewer({ planItems, className }: PlanViewerProps) {
  // Find the current task (lowest index where completed is false)
  const currentTaskIndex = planItems
    .filter((item) => !item.completed)
    .reduce(
      (minIndex, item) => (item.index < minIndex ? item.index : minIndex),
      Infinity,
    );

  const getTaskStatus = (item: PlanItem) => {
    if (item.completed) return "completed";
    if (item.index === currentTaskIndex) return "current";
    return "remaining";
  };

  return (
    <div className={cn("w-full space-y-3", className)}>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-sm font-medium text-gray-900">Execution Plan</h3>
        <span className="text-xs text-gray-500">
          {planItems.filter((item) => item.completed).length} of{" "}
          {planItems.length} completed
        </span>
      </div>

      <div className="space-y-2">
        {planItems
          .sort((a, b) => a.index - b.index) // Ensure proper ordering
          .map((item, idx) => {
            const status = getTaskStatus(item);

            return (
              <div
                key={`plan-item-${item.index}`}
                className={cn(
                  "flex items-start gap-3 rounded-lg border p-3 transition-colors",
                  {
                    "border-green-200 bg-green-50": status === "completed",
                    "border-blue-200 bg-blue-50": status === "current",
                    "border-gray-200 bg-gray-50": status === "remaining",
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
                  {status === "remaining" && (
                    <Clock className="h-4 w-4 text-gray-400" />
                  )}
                </div>

                {/* Task Content */}
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-500">
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
                  </div>

                  <p
                    className={cn("text-sm leading-relaxed", {
                      "text-gray-900": status === "current",
                      "text-gray-600":
                        status === "completed" || status === "remaining",
                    })}
                  >
                    {item.plan}
                  </p>

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
      </div>
    </div>
  );
}
