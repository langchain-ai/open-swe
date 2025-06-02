"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { CopyIcon, CheckIcon, RefreshCwIcon } from "lucide-react";
import { InstallAppButton } from "./InstallAppButton";

interface AgentTokenProviderProps {
  className?: string;
}

/**
 * Component to fetch and display a GitHub installation token for use with the AI agent
 * This token can be passed to your agent service to perform Git operations on behalf of the user
 */
export function AgentTokenProvider({
  className = "",
}: AgentTokenProviderProps) {
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [installationId, setInstallationId] = useState<string | null>(null);

  const fetchToken = async () => {
    setIsLoading(true);
    setError(null);
    setCopied(false);

    try {
      const response = await fetch("/api/github/token");

      if (!response.ok) {
        const errorData = await response.json();
        setError(errorData.error || "Failed to fetch token");
        setIsLoading(false);
        return;
      }

      const data = await response.json();
      setToken(data.token);
      setInstallationId(data.installation_id);
      setIsLoading(false);
    } catch (err) {
      setError("Network error when fetching token");
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchToken();
  }, []);

  const copyToken = () => {
    if (token) {
      navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (error && error.includes("installation")) {
    return (
      <div className={`rounded-md border p-4 ${className}`}>
        <h3 className="mb-2 text-lg font-medium">GitHub App Not Installed</h3>
        <p className="mb-4 text-sm text-gray-600">
          You need to install our GitHub App to generate tokens for the AI
          agent.
        </p>
        <InstallAppButton>Install GitHub App</InstallAppButton>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`rounded-md border p-4 ${className}`}>
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-800">{error}</p>
        </div>
        <Button
          variant="outline"
          onClick={fetchToken}
          disabled={isLoading}
        >
          Try Again
        </Button>
      </div>
    );
  }

  return (
    <div className={`rounded-md border p-4 ${className}`}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-medium">GitHub Token for AI Agent</h3>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchToken}
          disabled={isLoading}
        >
          <RefreshCwIcon className="mr-2 h-4 w-4" />
          Refresh Token
        </Button>
      </div>

      {isLoading ? (
        <div className="animate-pulse space-y-3">
          <div className="h-4 w-3/4 rounded bg-gray-200"></div>
          <div className="h-10 rounded bg-gray-200"></div>
        </div>
      ) : token ? (
        <>
          <p className="mb-2 text-sm text-gray-600">
            This token expires in 1 hour. Use it to authenticate your AI agent
            with GitHub.
          </p>
          <div className="relative">
            <div className="mb-2 overflow-x-auto rounded-md border bg-gray-50 p-3 font-mono text-sm whitespace-nowrap">
              {token}
            </div>
            <Button
              size="sm"
              variant="ghost"
              className="absolute top-2 right-2"
              onClick={copyToken}
            >
              {copied ? (
                <CheckIcon className="h-4 w-4" />
              ) : (
                <CopyIcon className="h-4 w-4" />
              )}
            </Button>
          </div>
          <div className="mt-4 space-y-2">
            <p className="text-sm font-medium">How to use this token:</p>
            <div className="rounded-md border bg-gray-50 p-3 font-mono text-xs">
              {`export GITHUB_TOKEN=${token}`}
            </div>
            <p className="text-xs text-gray-500">
              Pass this token to your agent service to perform Git operations on
              behalf of the user.
            </p>
          </div>
        </>
      ) : (
        <p className="text-gray-600">Loading token...</p>
      )}
    </div>
  );
}
