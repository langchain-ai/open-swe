import { useGitHubAppHook } from "@/hooks/useGitHubApp";
import { createContext, useContext, ReactNode } from "react";

type GitHubAppContextType = ReturnType<typeof useGitHubAppHook>;

const GitHubAppContext = createContext<GitHubAppContextType | undefined>(
  undefined,
);

export function GitHubAppProvider({ children }: { children: ReactNode }) {
  const value = useGitHubAppHook();
  console.log("value", value.branches);
  return (
    <GitHubAppContext.Provider value={value}>
      {children}
    </GitHubAppContext.Provider>
  );
}

export function useGitHubApp() {
  const context = useContext(GitHubAppContext);
  if (context === undefined) {
    throw new Error("useGitHubApp must be used within a GitHubAppProvider");
  }
  return context;
}
