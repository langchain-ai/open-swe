import React from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TargetRepository } from "@/providers/Stream";

const DUMMY_REPOSITORIES: TargetRepository[] = [
  { owner: "facebook", repo: "react" },
  { owner: "microsoft", repo: "vscode" },
  { owner: "vercel", repo: "next.js" },
  { owner: "nodejs", repo: "node" },
  { owner: "langchain-ai", repo: "open-swe" },
];

interface RepositorySelectorProps {
  value?: TargetRepository;
  onValueChange: (repository: TargetRepository) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function RepositorySelector({
  value,
  onValueChange,
  disabled = false,
  placeholder = "Select a repository...",
}: RepositorySelectorProps) {
  const handleValueChange = (repositoryKey: string) => {
    const repository = DUMMY_REPOSITORIES.find(
      (repo) => `${repo.owner}/${repo.repo}` === repositoryKey,
    );
    if (repository) {
      onValueChange(repository);
    }
  };

  const selectedValue = value ? `${value.owner}/${value.repo}` : undefined;

  return (
    <Select
      value={selectedValue}
      onValueChange={handleValueChange}
      disabled={disabled}
    >
      <SelectTrigger className="w-[200px]">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {DUMMY_REPOSITORIES.map((repo) => {
          const key = `${repo.owner}/${repo.repo}`;
          return (
            <SelectItem
              key={key}
              value={key}
            >
              {key}
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}
