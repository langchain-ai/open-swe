"use client";

import {
  CheckCircle,
  ChevronDown,
  FileText,
  Circle,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "../ui/button";

type PlanItem = {
  index: number;
  plan: string;
  completed: boolean;
};

type AcceptedPlanStepProps = {
  planTitle?: string;
  planItems?: PlanItem[];
  interruptType?: "accept" | "edit";
  collapse?: boolean;
};

export function AcceptedPlanStep({
  planTitle,
  planItems = [],
  interruptType,
  collapse: collapseProp = true,
}: AcceptedPlanStepProps) {
  const [collapsed, setCollapsed] = useState(collapseProp);

  const getStatusText = () => {
    if (interruptType === "edit") {
      return "Plan edited and accepted";
    }
    return "Plan accepted";
  };

  const getStatusIcon = () => {
    return <CheckCircle className="h-3.5 w-3.5 text-green-500" />;
  };

  const getPlanItemIcon = (item: PlanItem) => {
    if (item.completed) {
      return <CheckCircle className="h-3.5 w-3.5 text-green-500" />;
    }
    return (
      <Circle className="h-3.5 w-3.5 text-gray-400 dark:text-gray-500" />
    );
  };

  return (
    <div className="overflow-hidden rounded-md border border-gray-200 dark:border-gray-700">
      {/* Header */}
      <div className="relative flex items-center border-b border-gray-200 bg-gray-50 p-2 dark:border-gray-700 dark:bg-gray-800">
        <FileText className="mr-2 h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
        <span className="flex-1 text-xs font-normal text-gray-800 dark:text-gray-200">
          {getStatusText()}
        </span>
        {getStatusIcon()}
        <Button
          aria-label={collapsed ? "Expand" : "Collapse"}
          onClick={() => setCollapsed((c) => !c)}
          variant="ghost"
          size="icon"
        >
          <ChevronDown
            className={cn(
              "size-4 transition-transform",
              collapsed ? "rotate-0" : "rotate-180",
            )}
          />
        </Button>
      </div>
      
      {/* Content */}
      {!collapsed && (
        <div className="p-2">
          {planTitle && (
            <div className="mb-3 border-b border-gray-100 pb-2 dark:border-gray-700">
              <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">
                {planTitle}
              </h4>
            </div>
          )}
          
          {planItems.length > 0 && (
            <ul className="space-y-2">
              {planItems
                .sort((a, b) => a.index - b.index)
                .map((item) => (
                  <li
                    key={item.index}
                    className="flex items-start text-xs"
                  >
                    <span className="mr-2 mt-0.5 flex-shrink-0">
                      {getPlanItemIcon(item)}
                    </span>
                    <span className="font-normal text-gray-800 dark:text-gray-200">
                      {item.plan}
                    </span>
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

