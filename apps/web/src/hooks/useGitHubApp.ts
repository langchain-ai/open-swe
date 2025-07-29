import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useQueryState } from "nuqs";
import {
  Repository,
  getRepositoryBranches,
  Branch,
  searchBranch,
} from "@/utils/github";
import { getRepository } from "@/utils/github";
import type { TargetRepository } from "@open-swe/shared/open-swe/types";
import {
  useGitHubInstallations,
  type Installation,
} from "@/hooks/useGitHubInstallations";

const GITHUB_SELECTED_REPO_KEY = "selected-repository";

const saveRepositoryToLocalStorage = (repo: TargetRepository | null) => {
  try {
    if (repo) {
      localStorage.setItem(GITHUB_SELECTED_REPO_KEY, JSON.stringify(repo));
    } else {
      localStorage.removeItem(GITHUB_SELECTED_REPO_KEY);
    }
  } catch (error) {
    console.warn("Failed to save repository to localStorage:", error);
  }
};

const getRepositoryFromLocalStorage = (): TargetRepository | null => {
  try {
    const stored = localStorage.getItem(GITHUB_SELECTED_REPO_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (
        parsed &&
        typeof parsed.owner === "string" &&
        typeof parsed.repo === "string"
      ) {
        return {
          owner: parsed.owner,
          repo: parsed.repo,
          // Don't restore branch from localStorage
        };
      }
    }
    return null;
  } catch (error) {
    console.warn("Failed to retrieve repository from localStorage:", error);
    return null;
  }
};

interface UseGitHubAppReturn {
  // Installation and general state
  isInstalled: boolean | null;
  isLoading: boolean;
  error: string | null;

  // Installation management
  installations: Installation[];
  currentInstallation: Installation | null;
  installationsLoading: boolean;
  installationsError: string | null;
  switchInstallation: (installationId: string) => Promise<void>;
  refreshInstallations: () => Promise<void>;

  // Repository state and pagination
  repositories: Repository[];
  repositoriesPage: number;
  repositoriesHasMore: boolean;
  repositoriesLoadingMore: boolean;
  refreshRepositories: () => Promise<void>;
  loadMoreRepositories: () => Promise<void>;

  // Repository selection
  selectedRepository: TargetRepository | null;
  setSelectedRepository: (repo: TargetRepository | null) => void;

  // Branch state and pagination
  branches: Branch[];
  branchesPage: number;
  branchesHasMore: boolean;
  branchesLoading: boolean;
  branchesLoadingMore: boolean;
  branchesError: string | null;
  loadMoreBranches: () => Promise<void>;
  fetchBranches: () => Promise<void>;
  setBranchesPage: (page: number) => void;
  setBranches: (branches: Branch[]) => void;

  // Branch selection
  selectedBranch: string | null;
  setSelectedBranch: (branch: string | null) => void;
  refreshBranches: () => Promise<void>;
  searchForBranch: (branchName: string) => Promise<Branch | null>;

  // Repository metadata
  defaultBranch: string | null;
}

export function useGitHubApp(): UseGitHubAppReturn {
  // Use the centralized installation state
  const {
    currentInstallationId,
    installations,
    currentInstallation,
    isLoading: installationsLoading,
    error: installationsError,
    switchInstallation,
    refreshInstallations,
  } = useGitHubInstallations();

  // Installation and general state
  const [isInstalled, setIsInstalled] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Repository state and pagination
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [repositoriesPage, setRepositoriesPage] = useState(1);
  const [repositoriesHasMore, setRepositoriesHasMore] = useState(false);
  const [repositoriesLoadingMore, setRepositoriesLoadingMore] = useState(false);

  // Branch state and pagination
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchesPage, setBranchesPage] = useState(1);
  const [branchesHasMore, setBranchesHasMore] = useState(false);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchesLoadingMore, setBranchesLoadingMore] = useState(false);
  const [branchesError, setBranchesError] = useState<string | null>(null);

  // URL state management
  const [selectedRepositoryParam, setSelectedRepositoryParam] =
    useQueryState("repo");
  const [selectedBranchParam, setSelectedBranchParam] = useQueryState("branch");

  // Track if auto-selection has been attempted to prevent re-triggering
  const hasAutoSelectedRef = useRef(false);

  // Track if we've attempted to load from localStorage
  const hasCheckedLocalStorageRef = useRef(false);

  // Track previous installation ID to detect actual changes
  const previousInstallationIdRef = useRef<string | null>(null);

  const selectedRepository = useMemo(() => {
    if (!selectedRepositoryParam) return null;
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
  }, [selectedRepositoryParam, selectedBranchParam]);

  useEffect(() => {
    if (selectedRepository && !branchesLoading) {
      setBranches([]);
      setBranchesPage(1);
      fetchBranches();
    } else if (!selectedRepository) {
      setBranches([]);
      setSelectedBranchParam(null);
    }
  }, [selectedRepository]);

  const selectedBranch = selectedBranchParam;

  const setSelectedRepository = useCallback(
    (repo: TargetRepository | null) => {
      setSelectedRepositoryParam(repo ? `${repo.owner}/${repo.repo}` : null);
      // Persist to localStorage whenever repository is selected
      saveRepositoryToLocalStorage(repo);

      setSelectedBranchParam(null);
      setBranches([]);
      setBranchesPage(1);
      setBranchesHasMore(false);
    },
    [setSelectedRepositoryParam, setSelectedBranchParam],
  );

  const setSelectedBranch = (branch: string | null) => {
    setSelectedBranchParam(branch);
  };

  const checkInstallation = async (
    page: number = 1,
    append: boolean = false,
  ) => {
    if (!append) setIsLoading(true);
    if (append) setRepositoriesLoadingMore(true);
    setError(null);

    try {
      const response = await fetch(`/api/github/repositories?page=${page}`);

      if (response.ok) {
        const data = await response.json();
        const newRepositories = data.repositories || [];

        if (append) {
          setRepositories((prev) => [...prev, ...newRepositories]);
        } else {
          setRepositories(newRepositories);
        }

        setRepositoriesPage(data.pagination?.page || page);
        setRepositoriesHasMore(data.pagination?.hasMore || false);
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
      setRepositoriesLoadingMore(false);
    }
  };

  const fetchBranches = useCallback(
    async (page: number = 1, append: boolean = false) => {
      if (!selectedRepository) {
        setBranches([]);
        setBranchesPage(1);
        setBranchesHasMore(false);
        return;
      }

      if (!append) setBranchesLoading(true);
      if (append) setBranchesLoadingMore(true);
      setBranchesError(null);

      try {
        const branchData = await getRepositoryBranches(
          selectedRepository.owner,
          selectedRepository.repo,
          page,
        );

        if (append) {
          setBranches((prev) => {
            // Avoid adding duplicates
            const newBranches = branchData.branches.filter(
              (branch) => !prev.some((b) => b.name === branch.name),
            );
            return [...prev, ...newBranches];
          });
        } else {
          setBranches(branchData.branches);
        }

        setBranchesPage(page);
        setBranchesHasMore(branchData.hasMore);
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : "Failed to fetch branches";
        console.error(
          `Error fetching branches for ${selectedRepository.owner}/${selectedRepository.repo}:`,
          err,
        );
        setBranchesError(errorMessage);
      } finally {
        if (!append) setBranchesLoading(false);
        if (append) setBranchesLoadingMore(false);
      }
    },
    [selectedRepository?.owner, selectedRepository?.repo],
  );

  // Load more functions
  const loadMoreRepositories = useCallback(async () => {
    if (repositoriesHasMore && !repositoriesLoadingMore) {
      await checkInstallation(repositoriesPage + 1, true);
    }
  }, [repositoriesHasMore, repositoriesLoadingMore, repositoriesPage]);

  const loadMoreBranches = useCallback(async () => {
    if (branchesHasMore && !branchesLoadingMore) {
      await fetchBranches(branchesPage + 1, true);
    }
  }, [branchesHasMore, branchesLoadingMore, branchesPage, fetchBranches]);

  const searchForBranch = useCallback(
    async (branchName: string): Promise<Branch | null> => {
      if (!selectedRepository) {
        return null;
      }

      try {
        const branch = await searchBranch(
          selectedRepository.owner,
          selectedRepository.repo,
          branchName,
        );

        if (branch) {
          setBranches((prev) => {
            const exists = prev.some((b) => b.name === branch.name);
            if (!exists) {
              return [...prev, branch];
            }
            return prev;
          });
        }

        return branch;
      } catch (error) {
        console.error(`Error searching for branch ${branchName}:`, error);
        return null;
      }
    },
    [selectedRepository?.owner, selectedRepository?.repo],
  );

  // Refresh repositories when installation changes
  useEffect(() => {
    if (currentInstallationId) {
      const previousInstallationId = previousInstallationIdRef.current;

      // Only clear repository if installation actually changed to a different value
      if (
        previousInstallationId !== null &&
        previousInstallationId !== currentInstallationId
      ) {
        // Clear selected repository and branches when installation changes
        setSelectedRepository(null);
        setBranches([]);
        setRepositoriesPage(1);
        setRepositoriesHasMore(false);

        // Reset auto-selection flags so they can run again for the new installation
        hasAutoSelectedRef.current = false;
        hasCheckedLocalStorageRef.current = false;
      }

      previousInstallationIdRef.current = currentInstallationId;
    }

    checkInstallation();
  }, [currentInstallationId]);

  useEffect(() => {
    if (
      !hasCheckedLocalStorageRef.current &&
      !selectedRepository &&
      !isLoading &&
      !error &&
      isInstalled === true &&
      repositories.length > 0
    ) {
      hasCheckedLocalStorageRef.current = true;

      const storedRepo = getRepositoryFromLocalStorage();
      if (storedRepo) {
        const existsInResponse = repositories.some(
          (repo) => repo.full_name === `${storedRepo.owner}/${storedRepo.repo}`,
        );

        if (existsInResponse) {
          setSelectedRepository(storedRepo);
          hasAutoSelectedRef.current = true;
        } else {
          const fetchSpecificRepo = async () => {
            try {
              const specificRepo = await getRepository(
                storedRepo.owner,
                storedRepo.repo,
              );
              if (specificRepo) {
                setSelectedRepository(storedRepo);
                hasAutoSelectedRef.current = true;
              } else {
                const firstRepo = repositories[0];
                const targetRepo = {
                  owner: firstRepo.full_name.split("/")[0],
                  repo: firstRepo.full_name.split("/")[1],
                };
                setSelectedRepository(targetRepo);
                saveRepositoryToLocalStorage(targetRepo);
                hasAutoSelectedRef.current = true;
              }
            } catch (error) {
              console.warn("Failed to fetch specific repository:", error);
              const firstRepo = repositories[0];
              const targetRepo = {
                owner: firstRepo.full_name.split("/")[0],
                repo: firstRepo.full_name.split("/")[1],
              };
              setSelectedRepository(targetRepo);
              saveRepositoryToLocalStorage(targetRepo);
              hasAutoSelectedRef.current = true;
            }
          };

          fetchSpecificRepo();
        }
      }
    }
  }, [
    repositories,
    selectedRepository,
    isLoading,
    error,
    isInstalled,
    setSelectedRepository,
  ]);

  // Auto-select first repository on initial page load
  useEffect(() => {
    if (
      !hasAutoSelectedRef.current &&
      !selectedRepository &&
      !isLoading &&
      !error &&
      isInstalled === true &&
      repositories.length > 0 &&
      hasCheckedLocalStorageRef.current
    ) {
      const firstRepo = repositories[0];
      const targetRepo = {
        owner: firstRepo.full_name.split("/")[0],
        repo: firstRepo.full_name.split("/")[1],
      };
      setSelectedRepository(targetRepo);
      saveRepositoryToLocalStorage(targetRepo);
      hasAutoSelectedRef.current = true;
    }
  }, [
    repositories,
    selectedRepository,
    isLoading,
    error,
    isInstalled,
    setSelectedRepository,
    hasCheckedLocalStorageRef.current,
  ]);

  const refreshRepositories = async () => {
    // Reset pagination state on refresh
    setRepositoriesPage(1);
    setRepositoriesHasMore(false);
    await checkInstallation();
  };

  const refreshBranches = async () => {
    // Reset pagination state on refresh
    setBranchesPage(1);
    setBranchesHasMore(false);
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
    // Installation and general state
    isInstalled,
    isLoading,
    error,

    // Installation management
    installations,
    currentInstallation,
    installationsLoading,
    installationsError,
    switchInstallation,
    refreshInstallations,

    // Repository state and pagination
    repositories,
    repositoriesPage,
    repositoriesHasMore,
    repositoriesLoadingMore,
    refreshRepositories,
    loadMoreRepositories,

    // Repository selection
    selectedRepository,
    setSelectedRepository,

    // Branch state and pagination
    branches,
    branchesPage,
    branchesHasMore,
    branchesLoading,
    branchesLoadingMore,
    branchesError,
    loadMoreBranches,
    fetchBranches,

    // Branch selection
    selectedBranch,
    setSelectedBranch,
    refreshBranches,
    searchForBranch,
    setBranchesPage,
    setBranches,

    // Repository metadata
    defaultBranch,
  };
}
