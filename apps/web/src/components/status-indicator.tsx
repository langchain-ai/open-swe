"use client";
import { CheckCircle2, XCircle, LoaderCircle, Pause } from "lucide-react";

export const StatusIndicator = ({
  status,
  size = "default",
}: {
  status: "running" | "interrupted" | "done" | "error";
  size?: "default" | "sm";
}) => {
  const iconClass = size === "sm" ? "h-3 w-3" : "h-4 w-4";

  switch (status) {
    case "running":
      return (
        <LoaderCircle className={`${iconClass} animate-spin text-blue-500`} />
      );
    case "interrupted":
      return <Pause className={`${iconClass} text-yellow-500`} />;
    case "done":
      return <CheckCircle2 className={`${iconClass} text-green-500`} />;
    case "error":
      return <XCircle className={`${iconClass} text-red-500`} />;
    default:
      return null;
  }
};
