import { useState, useEffect } from "react";
import { Repository } from "@/utils/github";

interface UseGitHubAppReturn {
  isInstalled: boolean | null;
  isLoading: boolean;
  error: string | null;
  repositories: Repository[];
  refreshRepositories: () => Promise<void>;
}

export function useGitHubApp(): UseGitHubAppReturn {
  const [isInstalled, setIsInstalled] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);

  const checkInstallation = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch("/api/github/repositories");

      if (response.ok) {
        const data = await response.json();
        setRepositories(data.repositories || []);
        setIsInstalled(true);
      } else {
        const errorData = await response.json();
        if (errorData.error.includes("installation")) {
          setIsInstalled(false);
        } else {
          setError(errorData.error);
          setIsInstalled(false);
        }
      }
    } catch (err) {
      setError("Failed to check GitHub App installation status");
      setIsInstalled(false);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    checkInstallation();
  }, []);

  const refreshRepositories = async () => {
    await checkInstallation();
  };

  return {
    isInstalled,
    isLoading,
    error,
    repositories,
    refreshRepositories,
  };
}
