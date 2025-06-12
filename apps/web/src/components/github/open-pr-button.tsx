"use client";

import { useStreamContext } from "@/providers/Stream";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import { GitPullRequest } from "lucide-react";

export function OpenPRButton() {
  const stream = useStreamContext();
  
  // Only render if branchName exists in stream.values
  if (!stream.values?.branchName) {
    return null;
  }

  return (
    <TooltipIconButton
      tooltip="Open Pull Request"
      variant="ghost"
    >
      <GitPullRequest className="size-4" />
    </TooltipIconButton>
  );
}

