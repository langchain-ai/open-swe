import { useState, useCallback } from "react";

interface TokenResponse {
  token: string;
  installation_id: string;
}

interface UseGitHubTokenReturn {
  token: string | null;
  installationId: string | null;
  isLoading: boolean;
  error: string | null;
  fetchToken: () => Promise<string | null>;
}

/**
 * Hook to fetch a GitHub installation token that can be used for Git operations
 * This token can be passed to the agent service to perform Git operations on behalf of the user
 */
export function useGitHubToken(): UseGitHubTokenReturn {
  const [token, setToken] = useState<string | null>(null);
  const [installationId, setInstallationId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchToken = useCallback(async (): Promise<string | null> => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch("/api/github/token");

      if (!response.ok) {
        const errorData = await response.json();
        setError(errorData.error || "Failed to fetch token");
        setIsLoading(false);
        return null;
      }

      const data: TokenResponse = await response.json();
      setToken(data.token);
      setInstallationId(data.installation_id);
      setIsLoading(false);
      return data.token;
    } catch (err) {
      setError("Network error when fetching token");
      setIsLoading(false);
      return null;
    }
  }, []);

  return {
    token,
    installationId,
    isLoading,
    error,
    fetchToken,
  };
}
