"use client";

import { useGitHubApp } from "@/hooks/useGitHubApp";
import { createContext, useContext, ReactNode } from "react";

type GitHubAppContextType = ReturnType<typeof useGitHubApp>;

const GitHubAppContext = createContext<GitHubAppContextType | undefined>(
  undefined,
);

export function GitHubAppProvider({ children }: { children: ReactNode }) {
  const value = useGitHubApp();
  return (
    <GitHubAppContext.Provider value={value}>
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
