"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { TargetRepository } from "@openswe/shared/open-swe/types";

interface GitLabProject {
  id: number;
  name: string;
  path_with_namespace: string;
  default_branch: string;
  web_url: string;
  namespace: {
    id: number;
    name: string;
    path: string;
  };
}

interface GitLabProviderContextType {
  projects: GitLabProject[];
  projectsLoading: boolean;
  projectsError: Error | null;
  selectedProject: TargetRepository | null;
  selectProject: (projectPathWithNamespace: string) => void;
}

const GitLabProviderContext = createContext<
  GitLabProviderContextType | undefined
>(undefined);

export function GitLabProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<GitLabProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<Error | null>(null);
  const [selectedProject, setSelectedProject] =
    useState<TargetRepository | null>(null);

  useEffect(() => {
    const fetchProjects = async () => {
      try {
        const response = await fetch("/api/gitlab/projects");
        if (!response.ok) {
          throw new Error("Failed to fetch GitLab projects");
        }
        const data = await response.json();
        setProjects(data.projects);

        // Auto-select the first project if available
        if (data.projects.length > 0) {
          const firstProject = data.projects[0];
          const [owner, repo] = firstProject.path_with_namespace.split("/");
          setSelectedProject({
            owner,
            repo,
            branch: firstProject.default_branch,
          });
        }
      } catch (error) {
        console.error("Error fetching GitLab projects:", error);
        setProjectsError(
          error instanceof Error ? error : new Error("Unknown error")
        );
      } finally {
        setProjectsLoading(false);
      }
    };

    fetchProjects();
  }, []);

  const selectProject = (projectPathWithNamespace: string) => {
    const project = projects.find(
      (p) => p.path_with_namespace === projectPathWithNamespace
    );
    if (project) {
      const [owner, repo] = project.path_with_namespace.split("/");
      setSelectedProject({
        owner,
        repo,
        branch: project.default_branch,
      });
    }
  };

  return (
    <GitLabProviderContext.Provider
      value={{
        projects,
        projectsLoading,
        projectsError,
        selectedProject,
        selectProject,
      }}
    >
      {children}
    </GitLabProviderContext.Provider>
  );
}

export function useGitLabProvider() {
  const context = useContext(GitLabProviderContext);
  if (context === undefined) {
    throw new Error("useGitLabProvider must be used within a GitLabProvider");
  }
  return context;
}
