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
}

export function BranchSelector({
  disabled = false,
  placeholder = "Select a branch...",
}: BranchSelectorProps) {
  const [open, setOpen] = useState(false);
  const {
    branches,
    branchesLoading,
    branchesError,
    selectedBranch,
    setSelectedBranch,
    selectedRepository,
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
      // Only auto-select if no branch is currently selected or if the selected branch doesn't exist in the new repo
      const currentBranchExists =
        selectedBranch &&
        branches.some((branch) => branch.name === selectedBranch);

      if (!currentBranchExists) {
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
    selectedRepository,
    branchesLoading,
    branchesError,
    branches,
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
        className="max-w-[340px] justify-between"
      >
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4" />
          <span>Select a branch</span>
        </div>
      </Button>
    );
  }

  if (branchesLoading) {
    return (
      <Button
        variant="outline"
        disabled
        className="max-w-[340px] justify-between"
      >
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4" />
          <span>Loading branches...</span>
        </div>
      </Button>
    );
  }

  if (branchesError) {
    return (
      <Button
        variant="outline"
        disabled
        className="max-w-[340px] justify-between"
      >
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4" />
          <span>Error loading branches</span>
        </div>
      </Button>
    );
  }

  if (branches.length === 0) {
    return (
      <Button
        variant="outline"
        disabled
        className="max-w-[340px] justify-between"
      >
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4" />
          <span>No branches available</span>
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
          className="max-w-[300px] px-3"
          disabled={disabled}
        >
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <GitBranch className="h-4 w-4 shrink-0" />
              <span className="truncate text-left">
                {selectedBranch || placeholder}
              </span>
            </div>
            <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
          </div>
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
        </Command>
      </PopoverContent>
    </Popover>
  );
}
