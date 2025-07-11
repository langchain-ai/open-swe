import { useState, useEffect, useCallback } from "react";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";
import { getCookie } from "@/lib/utils";
import { Endpoints } from "@octokit/types";

type GitHubInstallationsResponse =
  Endpoints["GET /user/installations"]["response"]["data"];
type GitHubInstallation = GitHubInstallationsResponse["installations"][0];

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
  refreshCurrentInstallation: () => void;
  switchInstallation: (installationId: string) => Promise<void>;
}

/**
 * Transform GitHub API installation data to our simplified format
 */
const transformInstallation = (
  installation: GitHubInstallation,
): Installation => {
  if (!installation.account) {
    throw new Error("Installation account is null");
  }

  // Handle both User and Organization account types
  let accountName: string;
  if ("login" in installation.account && installation.account.login) {
    accountName = installation.account.login;
  } else if ("slug" in installation.account && installation.account.slug) {
    accountName = installation.account.slug;
  } else if ("name" in installation.account && installation.account.name) {
    accountName = installation.account.name;
  } else {
    accountName = "Unknown";
  }

  const accountType = installation.target_type as "User" | "Organization";

  return {
    id: installation.id,
    accountName,
    accountType,
    avatarUrl: installation.account.avatar_url,
  };
};

/**
 * Hook for managing GitHub App installations
 * Fetches installation data from the API endpoint and reads current installation ID from cookies
 * Provides functions to switch between installations
 */
export function useGitHubInstallations(): UseGitHubInstallationsReturn {
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentInstallationId, setCurrentInstallationId] = useState<
    string | null
  >(null);

  // Get current installation ID from the cookie
  const getCurrentInstallationId = useCallback((): string | null => {
    return getCookie(GITHUB_INSTALLATION_ID_COOKIE);
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

      // Get the current installation ID from the cookie
      const currentId = getCurrentInstallationId();
      setCurrentInstallationId(currentId);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to fetch installations";
      setError(errorMessage);
      setInstallations([]);
    } finally {
      setIsLoading(false);
    }
  }, [getCurrentInstallationId]);

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
  }, [installations, isLoading, currentInstallationId, switchInstallation]);

  // Initialize installation ID from cookie on mount
  useEffect(() => {
    const cookieInstallationId = getCurrentInstallationId();
    setCurrentInstallationId(cookieInstallationId);
  }, [getCurrentInstallationId]);

  // Initial fetch on mount
  useEffect(() => {
    fetchInstallations();
  }, [fetchInstallations]);

  // Refresh installations function
  const refreshInstallations = useCallback(async () => {
    await fetchInstallations();
  }, [fetchInstallations]);

  // Refresh current installation ID from cookie
  const refreshCurrentInstallation = useCallback(() => {
    const cookieInstallationId = getCurrentInstallationId();
    setCurrentInstallationId(cookieInstallationId);
  }, [getCurrentInstallationId]);

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
    refreshCurrentInstallation,
    switchInstallation,
  };
}
