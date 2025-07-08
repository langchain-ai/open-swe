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
  switchInstallation: (installationId: string) => void;
}

/**
 * Cookie utility functions for managing GITHUB_INSTALLATION_ID_COOKIE
 */
const getCookie = (name: string): string | null => {
  if (typeof document === "undefined") return null;

  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) {
    return parts.pop()?.split(";").shift() || null;
  }
  return null;
};

const setCookie = (name: string, value: string, days: number = 30): void => {
  if (typeof document === "undefined") return;

  const expires = new Date();
  expires.setTime(expires.getTime() + days * 24 * 60 * 60 * 1000);

  document.cookie = `${name}=${value}; expires=${expires.toUTCString()}; path=/; SameSite=Lax`;
};

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

  // Get current installation ID from cookie
  useEffect(() => {
    const installationId = getCookie(GITHUB_INSTALLATION_ID_COOKIE);
    setCurrentInstallationId(installationId);
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
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to fetch installations";
      setError(errorMessage);
      setInstallations([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Initial fetch on mount
  useEffect(() => {
    fetchInstallations();
  }, [fetchInstallations]);

  // Refresh installations function
  const refreshInstallations = useCallback(async () => {
    await fetchInstallations();
  }, [fetchInstallations]);

  // Switch installation function
  const switchInstallation = useCallback((installationId: string) => {
    // Update cookie
    setCookie(GITHUB_INSTALLATION_ID_COOKIE, installationId);

    // Update local state
    setCurrentInstallationId(installationId);
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
