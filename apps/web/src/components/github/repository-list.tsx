"use client";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { InstallationPrompt } from "./installation-prompt";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface RepositoryListProps {
  className?: string;
}

export function RepositoryList({ className = "" }: RepositoryListProps) {
  const { isInstalled, isLoading, error, repositories, refreshRepositories } =
    useGitHubAppProvider();

  if (isLoading) {
    return (
      <div className={cn("p-4", className)}>
        <div className="mb-4 h-6 w-24 animate-pulse rounded bg-gray-200"></div>
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="mb-4 rounded-md border p-4"
          >
            <div className="mb-2 h-5 w-48 animate-pulse rounded bg-gray-200"></div>
            <div className="h-4 w-64 animate-pulse rounded bg-gray-100"></div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn("p-4", className)}>
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-800">{error}</p>
        </div>
        <Button
          variant="outline"
          onClick={refreshRepositories}
        >
          Try Again
        </Button>
      </div>
    );
  }

  if (!isInstalled) {
    return (
      <div className={cn("p-4", className)}>
        <InstallationPrompt />
      </div>
    );
  }

  return (
    <div className={cn("p-4", className)}>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-medium">Your GitHub Repositories</h2>
        <Button
          variant="outline"
          size="sm"
          onClick={refreshRepositories}
          disabled={isLoading}
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {repositories.length === 0 ? (
        <div className="rounded-md border bg-gray-50 p-4 text-center">
          <p className="mb-4 text-gray-600">
            No repositories found. You may need to grant access to repositories
            in GitHub.
          </p>
          <a
            href="https://github.com/settings/installations"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-blue-600 hover:underline"
          >
            Manage GitHub App permissions
          </a>
        </div>
      ) : (
        <div className="space-y-3">
          {repositories.map((repo) => (
            <div
              key={repo.id}
              className="rounded-md border p-4 transition-colors hover:bg-gray-50"
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-medium">
                    <a
                      href={repo.html_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline"
                    >
                      {repo.full_name}
                    </a>
                  </h3>
                  <p className="mt-1 text-sm text-gray-600">
                    {repo.description || "No description"}
                  </p>
                  <div className="mt-2 flex items-center text-xs">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5",
                        repo.private
                          ? "bg-gray-100"
                          : "bg-green-100 text-green-800",
                      )}
                    >
                      {repo.private ? "Private" : "Public"}
                    </span>
                    <span className="text-gray-500">
                      Default branch: {repo.default_branch}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 border-t pt-4 text-sm text-gray-500">
        <p>
          Need to add or remove repositories?{" "}
          <a
            href="https://github.com/settings/installations"
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline"
          >
            Manage on GitHub
          </a>
        </p>
      </div>
    </div>
  );
}
