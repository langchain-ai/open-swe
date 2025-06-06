import { useState, useEffect } from "react";
import { useQueryState } from "nuqs";
import { Repository, getRepositoryBranches, Branch } from "@/utils/github";
import type { TargetRepository } from "../../../open-swe/src/types";

interface UseGitHubAppReturn {
  isInstalled: boolean | null;
  isLoading: boolean;
  error: string | null;
  repositories: Repository[];
  refreshRepositories: () => Promise<void>;
  selectedRepository: TargetRepository | null;
  setSelectedRepository: (repo: TargetRepository | null) => void;
  branches: Branch[];
  branchesLoading: boolean;
  branchesError: string | null;
  selectedBranch: string | null;
  setSelectedBranch: (branch: string | null) => void;
  refreshBranches: () => Promise<void>;
}

// Helper function to get GitHub OAuth access token from cookies
function getGitHubAccessToken(): string | null {
  if (typeof document === "undefined") return null;

  const cookies = document.cookie.split("; ");
  const tokenCookie = cookies.find((row) =>
    row.startsWith("x-github_access_token="),
  );
  return tokenCookie ? tokenCookie.split("=")[1] : null;
}

export function useGitHubApp(): UseGitHubAppReturn {
  const [isInstalled, setIsInstalled] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const [selectedRepositoryParam, setSelectedRepositoryParam] =
    useQueryState("repo");
  const [selectedBranchParam, setSelectedBranchParam] = useQueryState("branch");

  const selectedRepository = selectedRepositoryParam
    ? (() => {
        try {
          // Parse "owner/repo" format instead of JSON
          const parts = selectedRepositoryParam.split("/");
          if (parts.length === 2) {
            return {
              owner: parts[0],
              repo: parts[1],
              branch: selectedBranchParam || undefined,
            } as TargetRepository;
          }
          return null;
        } catch {
          return null;
        }
      })()
    : null;

  const selectedBranch = selectedBranchParam;

  const setSelectedRepository = (repo: TargetRepository | null) => {
    setSelectedRepositoryParam(repo ? `${repo.owner}/${repo.repo}` : null);
    // Clear branch when repository changes
    if (!repo) {
      setSelectedBranchParam(null);
      setBranches([]);
    }
  };

  const setSelectedBranch = (branch: string | null) => {
    setSelectedBranchParam(branch);
  };

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

  useEffect(() => {
    if (!selectedRepository) {
      setBranches([]);
      setSelectedBranchParam(null);
      return;
    }

    const accessToken = getGitHubAccessToken();
    if (!accessToken) {
      setBranchesError("GitHub access token not found");
      return;
    }

    setBranchesLoading(true);
    setBranchesError(null);

    const fetchBranches = async () => {
      try {
        const branchData = await getRepositoryBranches(
          selectedRepository.owner,
          selectedRepository.repo,
          accessToken,
        );
        setBranches(branchData || []);
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : "Failed to fetch branches";
        setBranchesError(errorMessage);
      } finally {
        setBranchesLoading(false);
      }
    };

    fetchBranches();
  }, [selectedRepository, setSelectedBranchParam]);

  const refreshRepositories = async () => {
    await checkInstallation();
  };

  const refreshBranches = async () => {
    if (!selectedRepository) return;

    const accessToken = getGitHubAccessToken();
    if (!accessToken) {
      setBranchesError("GitHub access token not found");
      return;
    }

    setBranchesLoading(true);
    setBranchesError(null);

    try {
      const branchData = await getRepositoryBranches(
        selectedRepository.owner,
        selectedRepository.repo,
        accessToken,
      );
      setBranches(branchData || []);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to fetch branches";
      setBranchesError(errorMessage);
    } finally {
      setBranchesLoading(false);
    }
  };

  return {
    isInstalled,
    isLoading,
    error,
    repositories,
    refreshRepositories,
    selectedRepository,
    setSelectedRepository,
    branches,
    branchesLoading,
    branchesError,
    selectedBranch,
    setSelectedBranch,
    refreshBranches,
  };
}
