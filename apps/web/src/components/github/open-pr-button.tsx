"use client";

import { useStreamContext } from "@/providers/Stream";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import { useQueryState } from "nuqs";
import {
  GitPullRequest,
  GitMerge,
  GitPullRequestClosed,
  GitPullRequestDraft,
} from "lucide-react";
import { useEffect, useState } from "react";
import { getPullRequest } from "@/utils/github";
import { cn } from "@/lib/utils";

export function OpenPRButton() {
  const stream = useStreamContext();
  const [branch] = useQueryState("branch");
  const [prState, setPrState] = useState<
    "open" | "closed" | "draft" | "merged"
  >();
  const [pullRequest, setPullRequest] = useState<Record<string, any>>();

  useEffect(() => {
    const baseBranch =
      stream.values.targetRepository?.branch ??
      stream.values.targetRepository?.baseCommit ??
      branch;
    if (
      !stream.values.branchName ||
      !stream.values.targetRepository ||
      !baseBranch
    ) {
      return;
    }
    getPullRequest({
      owner: stream.values.targetRepository.owner,
      repo: stream.values.targetRepository.repo,
      baseBranch: baseBranch,
      headBranch: stream.values.branchName,
    }).then((pr) => {
      if (!pr) return;
      setPullRequest(pr);
      if (pr.merged_at) {
        setPrState("merged");
      } else if (pr.draft) {
        setPrState("draft");
      } else {
        setPrState(pr.state);
      }
    });
  }, [stream.values?.branchName, stream.values.targetRepository, branch]);

  if (!pullRequest) {
    return null;
  }

  const handleOpenPR = () => {
    window.open(pullRequest.html_url, "_blank", "noopener,noreferrer");
  };

  return (
    <TooltipIconButton
      tooltip={`Open Pull Request #${pullRequest.number}`}
      variant="ghost"
      onClick={handleOpenPR}
      className={cn({
        "text-green-500 hover:text-green-600": prState === "open",
        "text-red-500 hover:text-red-600": prState === "closed",
        "text-gray-500 hover:text-gray-600": prState === "draft",
        "text-[#8957e5] hover:text-[#7847d1]": prState === "merged",
      })}
    >
      {prState === "merged" && <GitMerge className="size-4" />}
      {prState === "draft" && <GitPullRequestDraft className="size-4" />}
      {prState === "closed" && <GitPullRequestClosed className="size-4" />}
      {prState === "open" && <GitPullRequest className="size-4" />}
    </TooltipIconButton>
  );
}
