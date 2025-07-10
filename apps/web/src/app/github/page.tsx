"use client";

import { Suspense } from "react";
import { useGitHubApp } from "@/hooks/useGitHubApp";
import { GitHubAppProvider } from "@/providers/GitHubApp";
import { InstallationSelector } from "@/components/github/installation-selector";
import { GithubPageLoading } from "@/components/github/github-page-loading";

function GitHubPageContent() {
  const { repositories, isLoading, error, isInstalled, refreshRepositories } =
    useGitHubApp();

  const handleInstall = async () => {
    window.location.href = "/api/github/installation";
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-3xl font-bold">GitHub Repository Access</h1>
        {isInstalled && (
          <div className="flex items-center gap-4">
            <InstallationSelector />
          </div>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded border border-red-400 bg-red-100 px-4 py-3 text-red-700">
          {error}
        </div>
      )}

      {!isInstalled ? (
        <div className="mb-6 rounded-lg bg-white p-6 shadow-md dark:bg-gray-900">
          <h2 className="mb-4 text-xl font-semibold">Install GitHub App</h2>
          <p className="mb-4">
            To access your GitHub repositories, you need to install our GitHub
            App and grant it access to the repositories you want to use.
          </p>
          <button
            onClick={handleInstall}
            className="rounded bg-black px-4 py-2 text-white hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-200"
          >
            Install GitHub App
          </button>
        </div>
      ) : (
        <div className="mb-6 rounded-lg bg-white p-6 shadow-md dark:bg-gray-900">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold">Your Repositories</h2>
            <button
              onClick={refreshRepositories}
              disabled={isLoading}
              className="rounded bg-gray-200 px-4 py-2 hover:bg-gray-300 disabled:opacity-50 dark:bg-gray-700 dark:hover:bg-gray-600"
            >
              {isLoading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          {repositories.length === 0 && !isLoading ? (
            <p className="text-gray-600 dark:text-gray-400">
              No repositories found. Make sure you've granted access to at least
              one repository for the selected organization.
            </p>
          ) : isLoading ? (
            <p className="text-gray-600 dark:text-gray-400">
              Loading repositories...
            </p>
          ) : (
            <ul className="divide-y divide-gray-200 dark:divide-gray-700">
              {repositories.map((repo) => (
                <li
                  key={repo.id}
                  className="py-4"
                >
                  <div className="flex items-start">
                    <div>
                      <h3 className="font-medium">
                        <a
                          href={repo.html_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 hover:underline dark:text-blue-400"
                        >
                          {repo.full_name}
                        </a>
                      </h3>
                      <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
                        {repo.description || "No description"}
                      </p>
                      <div className="mt-2 flex items-center text-sm text-gray-500 dark:text-gray-400">
                        <span className="mr-4">
                          {repo.private ? "Private" : "Public"}
                        </span>
                        <span>Default branch: {repo.default_branch}</span>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="rounded-lg bg-white p-6 shadow-md dark:bg-gray-900">
        <h2 className="mb-4 text-xl font-semibold">Manage GitHub App</h2>
        <p className="mb-4 text-gray-600 dark:text-gray-400">
          You can manage your GitHub App installation, including adding or
          removing repositories, through GitHub.
        </p>
        <a
          href="https://github.com/settings/installations"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block rounded bg-gray-200 px-4 py-2 text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
        >
          Manage on GitHub
        </a>
      </div>
    </div>
  );
}

function GitHubPageWithProvider() {
  return (
    <GitHubAppProvider>
      <GitHubPageContent />
    </GitHubAppProvider>
  );
}

export default function GitHubPage() {
  return (
    <Suspense fallback={<GithubPageLoading />}>
      <GitHubPageWithProvider />
    </Suspense>
  );
}
