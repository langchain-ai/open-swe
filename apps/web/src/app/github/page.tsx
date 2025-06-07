"use client";

import { useState, useEffect } from "react";
import { Repository } from "@/utils/github";

export default function GitHubPage() {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isInstalled, setIsInstalled] = useState(false);

  useEffect(() => {
    const checkInstallation = async () => {
      try {
        const response = await fetch("/api/github/repositories");
        if (response.ok) {
          setIsInstalled(true);
          const data = await response.json();
          setRepositories(data.repositories || []);
        } else {
          const errorData = await response.json();
          if (errorData.error.includes("installation")) {
            setIsInstalled(false);
          } else {
            setError(errorData.error);
          }
        }
      } catch {
        setError("Failed to check installation status");
      }
    };

    checkInstallation();
  }, []);

  const handleInstall = async () => {
    window.location.href = "/api/github/installation";
  };

  const handleRefreshRepositories = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch("/api/github/repositories");
      if (response.ok) {
        const data = await response.json();
        setRepositories(data.repositories || []);
      } else {
        const errorData = await response.json();
        setError(errorData.error);
      }
    } catch {
      setError("Failed to fetch repositories");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="mb-6 text-3xl font-bold">GitHub Repository Access</h1>

      {error && (
        <div className="mb-4 rounded border border-red-400 bg-red-100 px-4 py-3 text-red-700">
          {error}
        </div>
      )}

      {!isInstalled ? (
        <div className="mb-6 rounded-lg bg-white p-6 shadow-md">
          <h2 className="mb-4 text-xl font-semibold">Install GitHub App</h2>
          <p className="mb-4">
            To access your GitHub repositories, you need to install our GitHub
            App and grant it access to the repositories you want to use.
          </p>
          <button
            onClick={handleInstall}
            className="rounded bg-black px-4 py-2 text-white hover:bg-gray-800"
          >
            Install GitHub App
          </button>
        </div>
      ) : (
        <div className="mb-6 rounded-lg bg-white p-6 shadow-md">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold">Your Repositories</h2>
            <button
              onClick={handleRefreshRepositories}
              disabled={loading}
              className="rounded bg-gray-200 px-4 py-2 hover:bg-gray-300 disabled:opacity-50"
            >
              {loading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          {repositories.length === 0 ? (
            <p>
              No repositories found. Make sure you've granted access to at least
              one repository.
            </p>
          ) : (
            <ul className="divide-y">
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
                          className="text-blue-600 hover:underline"
                        >
                          {repo.full_name}
                        </a>
                      </h3>
                      <p className="mt-1 text-sm text-gray-600">
                        {repo.description || "No description"}
                      </p>
                      <div className="mt-2 flex items-center text-sm">
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

      <div className="rounded-lg bg-white p-6 shadow-md">
        <h2 className="mb-4 text-xl font-semibold">Manage GitHub App</h2>
        <p className="mb-4">
          You can manage your GitHub App installation, including adding or
          removing repositories, through GitHub.
        </p>
        <a
          href="https://github.com/settings/installations"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block rounded bg-gray-200 px-4 py-2 text-gray-800 hover:bg-gray-300"
        >
          Manage on GitHub
        </a>
      </div>
    </div>
  );
}
