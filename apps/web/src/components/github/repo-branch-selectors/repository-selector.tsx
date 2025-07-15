import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import type { TargetRepository } from "@open-swe/shared/open-swe/types";
import { GitHubSVG } from "@/components/icons/github";
import { Repository } from "@/utils/github";
import { useGitHubAppProvider } from "@/providers/GitHubApp";

interface RepositorySelectorProps {
  disabled?: boolean;
  placeholder?: string;
  buttonClassName?: string;
  chatStarted?: boolean;
  streamTargetRepository?: TargetRepository;
}
// TODO: remove this, we should use the TargetRepository type from the open-swe package
// Convert GitHub Repository to TargetRepository format
const repositoryToTarget = (repo: Repository): TargetRepository => {
  const [owner, repoName] = repo.full_name.split("/");
  return { owner, repo: repoName };
};

// TODO: Add utility function for repoSelector while viewing a thread
// retrieve thread repo from nuqs params. set disabled = true.

export function RepositorySelector({
  disabled = false,
  placeholder = "Select a repository...",
  buttonClassName,
  chatStarted = false,
  streamTargetRepository,
}: RepositorySelectorProps) {
  const [open, setOpen] = useState(false);
  const {
    repositories,
    selectedRepository,
    setSelectedRepository,
    isLoading,
    error,
    isInstalled,
    repositoriesHasMore,
    repositoriesLoadingMore,
    loadMoreRepositories,
  } = useGitHubAppProvider();

  const handleSelect = (repositoryKey: string) => {
    const repository = repositories.find(
      (repo) => repo.full_name === repositoryKey,
    );
    if (repository) {
      setSelectedRepository(repositoryToTarget(repository));
      setOpen(false);
    }
  };

  const selectedValue = selectedRepository
    ? `${selectedRepository.owner}/${selectedRepository.repo}`
    : undefined;

  // When chatStarted and streamTargetRepository is available, use it for display
  const displayValue =
    chatStarted && streamTargetRepository
      ? `${streamTargetRepository.owner}/${streamTargetRepository.repo}`
      : selectedValue;

  if (isLoading) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <div className="flex items-center gap-2">
          <GitHubSVG
            width="16"
            height="16"
          />
          <span>Loading repositories...</span>
        </div>
      </Button>
    );
  }

  if (error) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <div className="flex items-center gap-2">
          <GitHubSVG
            width="16"
            height="16"
          />
          <span>Error loading repositories</span>
        </div>
      </Button>
    );
  }

  if (isInstalled === false) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <div className="flex items-center gap-2">
          <GitHubSVG
            width="16"
            height="16"
          />
          <span>GitHub App not installed</span>
        </div>
      </Button>
    );
  }

  if (repositories.length === 0) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <div className="flex items-center gap-2">
          <GitHubSVG
            width="16"
            height="16"
          />
          <span>No repositories available</span>
        </div>
      </Button>
    );
  }

  if (chatStarted) {
    return (
      <Button
        variant="outline"
        className={cn(buttonClassName)}
        size="sm"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <GitHubSVG />
          <span className="truncate text-left">
            {displayValue || placeholder}
          </span>
        </div>
      </Button>
    );
  }

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
    >
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn(buttonClassName)}
          disabled={disabled}
          size="sm"
        >
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <GitHubSVG />
            <span className="truncate text-left">
              {selectedValue || placeholder}
            </span>
          </div>
          <ChevronsUpDown className="h-4 w-4 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[340px] p-0">
        <Command>
          <CommandInput placeholder="Search repositories..." />
          <CommandList>
            <CommandEmpty>No repositories found.</CommandEmpty>
            <CommandGroup>
              {repositories.map((repo) => {
                const key = repo.full_name;
                const isSelected = selectedValue === key;
                return (
                  <CommandItem
                    key={repo.id}
                    value={key}
                    onSelect={() => handleSelect(key)}
                  >
                    <Check
                      className={cn(
                        "mr-2 h-4 w-4",
                        isSelected ? "opacity-100" : "opacity-0",
                      )}
                    />
                    <div className="flex flex-col">
                      <span className="font-medium">{repo.full_name}</span>
                    </div>
                  </CommandItem>
                );
              })}
            </CommandGroup>
            {repositoriesHasMore && (
              <CommandGroup>
                <CommandItem
                  onSelect={() => {
                    loadMoreRepositories();
                  }}
                  disabled={repositoriesLoadingMore}
                  className="justify-center"
                >
                  {repositoriesLoadingMore
                    ? "Loading more..."
                    : "Load more repositories"}
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
