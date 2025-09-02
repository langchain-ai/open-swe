"use client";

import { useGitHubApp } from "@/hooks/useGitHubApp";
import { createContext, useContext, ReactNode } from "react";

const GITHUB_DISABLED = process.env.NEXT_PUBLIC_GITHUB_DISABLED === "true";

type GitHubAppContextType = ReturnType<typeof useGitHubApp>;

const GitHubAppContext = createContext<GitHubAppContextType | undefined>(
  undefined,
);

export function GitHubAppProvider({ children }: { children: ReactNode }) {
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const value = GITHUB_DISABLED ? undefined : useGitHubApp();
  const finalValue: GitHubAppContextType = value ?? {
    isInstalled: null,
    isLoading: false,
    error: null,
    installations: [],
    currentInstallation: null,
    installationsLoading: false,
    installationsError: null,
    switchInstallation: async () => {},
    refreshInstallations: async () => {},
    repositories: [],
    repositoriesPage: 0,
    repositoriesHasMore: false,
    repositoriesLoadingMore: false,
    refreshRepositories: async () => {},
    loadMoreRepositories: async () => {},
    selectedRepository: null,
    setSelectedRepository: () => {},
    branches: [],
    branchesPage: 0,
    branchesHasMore: false,
    branchesLoading: false,
    branchesLoadingMore: false,
    branchesError: null,
    loadMoreBranches: async () => {},
    fetchBranches: async () => {},
    setBranchesPage: () => {},
    setBranches: () => {},
    selectedBranch: null,
    setSelectedBranch: () => {},
    refreshBranches: async () => {},
    searchForBranch: async () => null,
    defaultBranch: null,
  };

  return (
    <GitHubAppContext.Provider value={finalValue}>
      {children}
    </GitHubAppContext.Provider>
  );
}

export function useGitHubAppProvider() {
  const context = useContext(GitHubAppContext);
  if (context === undefined) {
    throw new Error(
      "useGitHubAppProvider must be used within a GitHubAppProvider",
    );
  }
  return context;
}
