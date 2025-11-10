"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useGitLabProvider } from "@/providers/GitLab";
import { GitBranch, Loader2 } from "lucide-react";

export function GitLabProjectSelector() {
  const { projects, projectsLoading, selectedProject, selectProject } =
    useGitLabProvider();

  if (projectsLoading) {
    return (
      <div className="border-border bg-background/50 flex items-center gap-1 rounded-md border p-1">
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
        <span className="text-muted-foreground text-xs">Loading projects...</span>
      </div>
    );
  }

  if (!projects || projects.length === 0) {
    return (
      <div className="border-border bg-background/50 flex items-center gap-1 rounded-md border p-1">
        <span className="text-muted-foreground text-xs">No projects found</span>
      </div>
    );
  }

  const selectedProjectPath = selectedProject
    ? `${selectedProject.owner}/${selectedProject.repo}`
    : undefined;

  return (
    <div className="flex items-center gap-1">
      {/* Project Selector */}
      <Select value={selectedProjectPath} onValueChange={selectProject}>
        <SelectTrigger className="border-border bg-background/50 h-auto min-w-[120px] rounded-md border p-1 text-xs">
          <SelectValue placeholder="Select project">
            {selectedProject && (
              <span className="text-muted-foreground">
                {selectedProject.owner}/{selectedProject.repo}
              </span>
            )}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {projects.map((project) => (
            <SelectItem
              key={project.id}
              value={project.path_with_namespace}
              className="text-xs"
            >
              {project.path_with_namespace}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Branch Display */}
      {selectedProject && (
        <div className="border-border bg-background/50 flex items-center gap-1 rounded-md border p-1">
          <GitBranch className="h-3 w-3 text-muted-foreground" />
          <span className="text-muted-foreground text-xs">
            {selectedProject.branch}
          </span>
        </div>
      )}
    </div>
  );
}
