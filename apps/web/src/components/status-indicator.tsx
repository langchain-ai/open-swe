"use client";

import { XCircle, LoaderCircle, Pause, Circle } from "lucide-react";
import { ThreadStatus } from "@langchain/langgraph-sdk";
import { cn } from "@/lib/utils";

export const StatusIndicator = ({
  status,
  size = "default",
}: {
  status: ThreadStatus;
  size?: "default" | "sm";
}) => {
  const iconClass = size === "sm" ? "h-3 w-3" : "h-4 w-4";

  switch (status) {
    case "busy":
      return (
        <LoaderCircle className={cn("animate-spin text-blue-500", iconClass)} />
      );
    case "interrupted":
      return <Pause className={cn("text-yellow-500", iconClass)} />;
    case "idle":
      return <Circle className={cn("text-gray-400", iconClass)} />;
    case "error":
      return <XCircle className={cn("text-red-500", iconClass)} />;
    default:
      return null;
  }
};
