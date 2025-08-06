"use client";

import { useState, useEffect } from "react";
import { X, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { useGitHubAppProvider } from "@/providers/GitHubApp";

const FORKED_REPOSITORY_BANNER_DISMISSED_KEY =
  "forked_repository_banner_dismissed";

export function ForkedRepositoryBanner() {
  const { selectedRepository, repositories } = useGitHubAppProvider();
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    // Check if user has previously dismissed the banner
    const hasDismissed = localStorage.getItem(
      FORKED_REPOSITORY_BANNER_DISMISSED_KEY,
    );
    if (hasDismissed === "true") {
      setDismissed(true);
    }
  }, []);

  // Find the selected repository in the repositories list to check if it's a fork
  const currentRepo = repositories.find(
    (repo) =>
      selectedRepository &&
      repo.full_name ===
        `${selectedRepository.owner}/${selectedRepository.repo}`,
  );

  // Don't show banner if:
  // - User has dismissed the banner
  // - No repository is selected
  // - Current repository is not a fork
  if (dismissed || !selectedRepository || !currentRepo?.fork) {
    return null;
  }

  const handleDismiss = () => {
    if (typeof window === "undefined") {
      return;
    }
    setDismissed(true);
    localStorage.setItem(FORKED_REPOSITORY_BANNER_DISMISSED_KEY, "true");
  };

  return (
    <Alert
      variant="warning"
      className="relative"
    >
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Forked Repository Detected</AlertTitle>
      <AlertDescription>
        Open SWE does not currently work with forked repositories since issues
        cannot be created on them. Please select the original repository or
        create your own repository to use Open SWE.
      </AlertDescription>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleDismiss}
        className="absolute top-2 right-2 h-8 w-8 p-0 text-yellow-600 hover:text-yellow-800 dark:text-yellow-400 dark:hover:text-yellow-200"
      >
        <X className="h-4 w-4" />
      </Button>
    </Alert>
  );
}
