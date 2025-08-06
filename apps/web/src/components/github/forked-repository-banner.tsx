"use client";

import { AlertTriangle } from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { repoHasIssuesEnabled } from "@/lib/repo-has-issues";

const GITHUB_DOCS_LINK_ENABLING_ISSUES =
  "https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/disabling-issues";

export function IssuesRequiredBanner() {
  const { selectedRepository, repositories } = useGitHubAppProvider();

  const currentRepo = repositories.find(
    (repo) =>
      selectedRepository &&
      repo.full_name ===
        `${selectedRepository.owner}/${selectedRepository.repo}`,
  );

  // If the repo has issues enabled, we support it.
  if (
    !selectedRepository ||
    !currentRepo ||
    repoHasIssuesEnabled(currentRepo)
  ) {
    return null;
  }

  return (
    <Alert
      variant="warning"
      className="relative"
    >
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Issues Must Be Enabled</AlertTitle>
      <AlertDescription>
        <p>
          Open SWE requires issues to be enabled on the repository. Please
          enable issues on the repository to use Open SWE.
        </p>
        <p>
          See{" "}
          <a
            className="font-semibold underline underline-offset-2"
            href={GITHUB_DOCS_LINK_ENABLING_ISSUES}
            target="_blank"
          >
            here
          </a>{" "}
          for how to enable issues (docs show how to disable them, but the
          process is the same).
        </p>
      </AlertDescription>
    </Alert>
  );
}
