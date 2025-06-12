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
import { useState, useEffect } from "react";
import { useGitHubApp } from "@/hooks/useGitHubApp";
import { GitBranch, Shield } from "lucide-react";

interface BranchSelectorProps {
  disabled?: boolean;
  placeholder?: string;
  buttonClassName?: string;
  chatStarted?: boolean;
}

export function BranchSelector({
  disabled = false,
  placeholder = "Select a branch...",
  buttonClassName,
  chatStarted = false,
}: BranchSelectorProps) {
  const [open, setOpen] = useState(false);
  const {
    branches,
    branchesLoading,
    branchesError,
    selectedBranch,
    setSelectedBranch,
    selectedRepository,
    branchesHasMore,
    branchesLoadingMore,
    loadMoreBranches,
    defaultBranch,
  } = useGitHubApp();

  // Auto-select default branch when repository changes and branches are loaded
  useEffect(() => {
    if (
      selectedRepository &&
      !branchesLoading &&
      !branchesError &&
      branches.length > 0
    ) {
      // Check if the current selected branch exists in the new repository
      const currentBranchExists =
        selectedBranch &&
        branches.some((branch) => branch.name === selectedBranch);

      // Auto-select default branch if no branch is selected OR if the selected branch doesn't exist in this repo
      if (!selectedBranch || !currentBranchExists) {
        // Try to find the repository's actual default branch first
        const actualDefaultBranch = defaultBranch
          ? branches.find((branch) => branch.name === defaultBranch)
          : null;

        if (actualDefaultBranch) {
          setSelectedBranch(actualDefaultBranch.name);
        } else if (branches.length > 0) {
          // If default branch doesn't exist in branches list, select the first available branch
          setSelectedBranch(branches[0].name);
        }
      }
    }
  }, [
    selectedRepository?.owner,
    selectedRepository?.repo,
    branchesLoading,
    branchesError,
    branches.length,
    selectedBranch,
    setSelectedBranch,
    defaultBranch,
  ]);

  const handleSelect = (branchName: string) => {
    setSelectedBranch(branchName);
    setOpen(false);
  };

  if (!selectedRepository) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <GitBranch />
        <span>Select a branch</span>
      </Button>
    );
  }

  if (branchesLoading) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <GitBranch />
        <span>Loading branches...</span>
      </Button>
    );
  }

  if (branchesError) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <GitBranch />
        <span>Error loading branches</span>
      </Button>
    );
  }

  if (branches.length === 0) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <GitBranch />
        <span>
          No branches available {branchesError && `(${branchesError})`}
        </span>
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
          <GitBranch />
          <span className="truncate text-left">
            {selectedBranch || placeholder}
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
          size="sm"
          disabled={disabled}
        >
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <GitBranch />
            <span className="truncate text-left">
              {selectedBranch || placeholder}
            </span>
          </div>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[340px] p-0">
        <Command>
          <CommandInput placeholder="Search branches..." />
          <CommandList>
            <CommandEmpty>No branches found.</CommandEmpty>
            <CommandGroup>
              {branches
                .slice()
                .sort((a, b) => {
                  if (defaultBranch) {
                    if (a.name === defaultBranch) return -1;
                    if (b.name === defaultBranch) return 1;
                  }
                  return 0;
                })
                .map((branch) => {
                  const isSelected = selectedBranch === branch.name;
                  const isDefault = branch.name === defaultBranch;
                  return (
                    <CommandItem
                      key={branch.name}
                      value={branch.name}
                      onSelect={() => handleSelect(branch.name)}
                    >
                      <Check
                        className={cn(
                          "mr-2 h-4 w-4",
                          isSelected ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <div className="flex items-center gap-2">
                        <GitBranch className="h-3 w-3" />
                        <span className="font-medium">{branch.name}</span>
                        {isDefault && (
                          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700">
                            default
                          </span>
                        )}
                        {branch.protected && (
                          <div title="Protected branch">
                            <Shield className="h-3 w-3 text-amber-500" />
                          </div>
                        )}
                      </div>
                    </CommandItem>
                  );
                })}
            </CommandGroup>
          </CommandList>
          {branchesHasMore && (
            <Button
              onClick={loadMoreBranches}
              disabled={branchesLoadingMore}
              className="mx-auto my-2 flex"
              variant="outline"
            >
              {branchesLoadingMore ? "Loading more..." : "Load more"}
            </Button>
          )}
        </Command>
      </PopoverContent>
    </Popover>
  );
}
