import { useState, useEffect, useCallback } from "react";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";
import type {
  GitHubInstallation,
  GitHubInstallationsResponse,
} from "@/app/api/github/installations/route";

export interface Installation {
  id: number;
  accountName: string;
  accountType: "User" | "Organization";
  avatarUrl: string;
}

interface UseGitHubInstallationsReturn {
  // Installation data
  installations: Installation[];
  currentInstallationId: string | null;
  currentInstallation: Installation | null;

  // State management
  isLoading: boolean;
  error: string | null;

  // Actions
  refreshInstallations: () => Promise<void>;
  switchInstallation: (installationId: string) => Promise<void>;
}

/**
 * Transform GitHub API installation data to our simplified format
 */
const transformInstallation = (
  installation: GitHubInstallation,
): Installation => ({
  id: installation.id,
  accountName: installation.account.login,
  accountType: installation.account.type,
  avatarUrl: installation.account.avatar_url,
});

/**
 * Hook for managing GitHub App installations
 * Fetches installation data from the API endpoint and provides functions to switch between installations
 */
export function useGitHubInstallations(): UseGitHubInstallationsReturn {
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentInstallationId, setCurrentInstallationId] = useState<
    string | null
  >(null);

  // Fetch current installation ID from the server (since it's in an HttpOnly cookie)
  const fetchCurrentInstallationId = useCallback(async (): Promise<
    string | null
  > => {
    try {
      const response = await fetch("/api/github/current-installation");
      if (response.ok) {
        const data = await response.json();
        return data.installationId || null;
      }
      return null;
    } catch (error) {
      console.warn("Failed to fetch current installation ID:", error);
      return null;
    }
  }, []);

  // Fetch installations from API
  const fetchInstallations = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const response = await fetch("/api/github/installations");

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || `HTTP ${response.status}`);
      }

      const data: GitHubInstallationsResponse = await response.json();
      const transformedInstallations = data.installations.map(
        transformInstallation,
      );

      setInstallations(transformedInstallations);

      // Also fetch the current installation ID from the server
      const currentId = await fetchCurrentInstallationId();
      setCurrentInstallationId(currentId);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to fetch installations";
      setError(errorMessage);
      setInstallations([]);
    } finally {
      setIsLoading(false);
    }
  }, [fetchCurrentInstallationId]);

  // Auto-select default installation when installations are loaded
  useEffect(() => {
    if (installations.length > 0 && !isLoading) {
      // Check if current installation ID is valid
      const isCurrentInstallationValid =
        currentInstallationId &&
        installations.some(
          (installation) =>
            installation.id.toString() === currentInstallationId,
        );

      if (!isCurrentInstallationValid) {
        // No valid installation selected, auto-select the first one
        const firstInstallation = installations[0];
        if (firstInstallation) {
          switchInstallation(firstInstallation.id.toString());
        }
      }
    }
  }, [installations, isLoading, currentInstallationId]);

  // Initial fetch on mount
  useEffect(() => {
    fetchInstallations();
  }, [fetchInstallations]);

  // Refresh installations function
  const refreshInstallations = useCallback(async () => {
    await fetchInstallations();
  }, [fetchInstallations]);

  // Switch installation function - now uses API endpoint
  const switchInstallation = useCallback(async (installationId: string) => {
    try {
      const response = await fetch("/api/github/switch-installation", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ installationId }),
      });

      if (response.ok) {
        // Update local state immediately for responsive UI
        setCurrentInstallationId(installationId);
      } else {
        console.error("Failed to switch installation");
      }
    } catch (error) {
      console.error("Error switching installation:", error);
    }
  }, []);

  // Find current installation object
  const currentInstallation = currentInstallationId
    ? installations.find(
        (installation) => installation.id.toString() === currentInstallationId,
      ) || null
    : null;

  return {
    // Installation data
    installations,
    currentInstallationId,
    currentInstallation,

    // State management
    isLoading,
    error,

    // Actions
    refreshInstallations,
    switchInstallation,
  };
}
