"use client";

import { useStreamContext } from "@/providers/Stream";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import { useQueryState } from "nuqs";
import { GitPullRequest } from "lucide-react";

export function OpenPRButton() {
  const stream = useStreamContext();
  
  // Only render if branchName exists in stream.values
  if (!stream.values?.branchName) {
    return null;
  }

  // Get query parameters
  const [repo] = useQueryState("repo");
  const [baseBranch] = useQueryState("base-branch");

  // Extract owner and repo name from the repo query parameter
  // Expected format: "owner/repo"
  const ownerRepo = repo?.split("/");
  const owner = ownerRepo?.[0];
  const repoName = ownerRepo?.[1];

  // Generate the PR comparison URL
  const generatePRUrl = () => {
    if (!owner || !repoName || !baseBranch || !stream.values.branchName) {
      return null;
    }
    return `https://github.com/${owner}/${repoName}/compare/${baseBranch}...${stream.values.branchName}`;
  };

  const handleOpenPR = () => {
    const url = generatePRUrl();
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  };

  return (
    <TooltipIconButton
      tooltip="Open Pull Request"
      variant="ghost"
      onClick={handleOpenPR}
    >
      <GitPullRequest className="size-4" />
    </TooltipIconButton>
  );
}


