"use client";
import { XCircle, LoaderCircle, Pause, Circle } from "lucide-react";
import { ThreadStatus } from "@langchain/langgraph-sdk";

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
        <LoaderCircle className={`${iconClass} animate-spin text-blue-500`} />
      );
    case "interrupted":
      return <Pause className={`${iconClass} text-yellow-500`} />;
    case "idle":
      return <Circle className={`${iconClass} text-gray-400`} />;
    case "error":
      return <XCircle className={`${iconClass} text-red-500`} />;
    default:
      return null;
  }
};
