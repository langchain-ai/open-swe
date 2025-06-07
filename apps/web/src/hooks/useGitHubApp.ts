import { useState, useEffect, useRef, useCallback } from "react";
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
  defaultBranch: string | null;
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

  // Track if auto-selection has been attempted to prevent re-triggering
  const hasAutoSelectedRef = useRef(false);

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

  const setSelectedRepository = useCallback(
    (repo: TargetRepository | null) => {
      setSelectedRepositoryParam(repo ? `${repo.owner}/${repo.repo}` : null);
      if (!repo) {
        setSelectedBranchParam(null);
        setBranches([]);
      }
    },
    [setSelectedRepositoryParam, setSelectedBranchParam],
  );

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
        console.log("Fetched repositories:", data.repositories);
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
    } catch {
      setError("Failed to check GitHub App installation status");
      setIsInstalled(false);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchBranches = useCallback(async () => {
    if (!selectedRepository) {
      setBranches([]);
      return;
    }

    const accessToken = getGitHubAccessToken();
    if (!accessToken) {
      setBranchesError("GitHub access token not found");
      return;
    }

    setBranchesLoading(true);
    setBranchesError(null);

    try {
      console.log(
        `Fetching branches for ${selectedRepository.owner}/${selectedRepository.repo}`,
      );
      const branchData = await getRepositoryBranches(
        selectedRepository.owner,
        selectedRepository.repo,
        accessToken,
      );
      console.log(`Fetched ${branchData?.length || 0} branches:`, branchData);
      setBranches(branchData || []);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to fetch branches";
      console.error(
        `Error fetching branches for ${selectedRepository.owner}/${selectedRepository.repo}:`,
        err,
      );
      setBranchesError(errorMessage);
    } finally {
      setBranchesLoading(false);
    }
  }, [selectedRepository]);

  useEffect(() => {
    checkInstallation();
  }, []);

  useEffect(() => {
    if (selectedRepository) {
      fetchBranches();
    } else {
      setBranches([]);
      setSelectedBranchParam(null);
    }
  }, [
    selectedRepository?.owner,
    selectedRepository?.repo,
    fetchBranches,
    setSelectedBranchParam,
  ]);

  // Auto-select first repository on initial page load
  useEffect(() => {
    if (
      !hasAutoSelectedRef.current && // Haven't auto-selected yet
      !selectedRepository && // No repo currently selected
      !isLoading && // Not loading repositories
      !error && // No error occurred
      isInstalled === true && // GitHub App is installed
      repositories.length > 0 // Repositories are available
    ) {
      const firstRepo = repositories[0];
      const targetRepo = {
        owner: firstRepo.full_name.split("/")[0],
        repo: firstRepo.full_name.split("/")[1],
      };
      setSelectedRepository(targetRepo);
      hasAutoSelectedRef.current = true;
    }
  }, [
    repositories,
    selectedRepository,
    isLoading,
    error,
    isInstalled,
    setSelectedRepository,
  ]);

  const refreshRepositories = async () => {
    await checkInstallation();
  };

  const refreshBranches = async () => {
    await fetchBranches();
  };

  // Get the default branch for the currently selected repository
  const defaultBranch = selectedRepository
    ? repositories.find(
        (repo) =>
          repo.full_name ===
          `${selectedRepository.owner}/${selectedRepository.repo}`,
      )?.default_branch || null
    : null;

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
    defaultBranch,
  };
}
