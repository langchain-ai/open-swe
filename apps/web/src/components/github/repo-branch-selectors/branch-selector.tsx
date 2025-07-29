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
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { GitBranch, Shield } from "lucide-react";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { Branch } from "@/utils/github";
import { toast } from "sonner";
import { defaultFilter } from "cmdk";

interface BranchSelectorProps {
  disabled?: boolean;
  placeholder?: string;
  buttonClassName?: string;
  chatStarted?: boolean;
  streamTargetRepository?: TargetRepository;
}

const findDefaultBranch = (
  branches: Branch[],
  defaultBranch: string | null,
) => {
  const actualDefaultBranch = defaultBranch
    ? branches.find((branch) => branch.name === defaultBranch)
    : null;

  return actualDefaultBranch
    ? actualDefaultBranch.name
    : branches.length > 0
      ? branches[0].name
      : null;
};

export function BranchSelector({
  disabled = false,
  placeholder = "Select a branch...",
  buttonClassName,
  chatStarted = false,
  streamTargetRepository,
}: BranchSelectorProps) {
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearching, setIsSearching] = useState(false);
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
    searchForBranch,
    defaultBranch,
  } = useGitHubAppProvider();

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

      if (selectedBranch && !currentBranchExists) {
        searchForBranch(selectedBranch).then((b) => {
          if (b) {
            // branch was found after search. can return early
            return;
          }
          setSelectedBranch(findDefaultBranch(branches, defaultBranch));
        });
      } else if (!selectedBranch) {
        setSelectedBranch(findDefaultBranch(branches, defaultBranch));
      }
    }
  }, [
    selectedRepository?.owner,
    selectedRepository?.repo,
    branchesLoading,
    branchesError,
    selectedBranch,
    setSelectedBranch,
    defaultBranch,
  ]);

  const handleSelect = (branchName: string) => {
    setSelectedBranch(branchName);
    setOpen(false);
  };

  const handleSearchForBranch = async () => {
    if (!searchQuery.trim() || !selectedRepository) return;

    setIsSearching(true);
    try {
      const foundBranch = await searchForBranch(searchQuery.trim());
      if (!foundBranch) {
        toast.warning(`Branch "${searchQuery.trim()}" not found`);
      }
    } finally {
      setIsSearching(false);
    }
  };

  // Whether or not to allow explicitly searching for a branch, even when there are results.
  const allowExplicitSearchReqWithResults = !!(
    searchQuery.trim() &&
    branches.some(
      (branch) => defaultFilter(branch.name, searchQuery.trim()) > 0,
    )
  );

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

  if (branchesLoading && !branches.length) {
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

  // Determine the display value - prioritize stream data when chatStarted and available
  const displayValue =
    chatStarted && streamTargetRepository?.branch
      ? streamTargetRepository.branch
      : selectedBranch;

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
          size="sm"
          disabled={disabled}
        >
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <GitBranch />
            <span className="truncate text-left">
              {selectedBranch || placeholder}
            </span>
          </div>
          <ChevronsUpDown className="h-4 w-4 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[340px] p-0">
        <Command>
          <CommandInput
            placeholder="Search branches..."
            value={searchQuery}
            onValueChange={setSearchQuery}
          />
          <CommandList>
            <CommandEmpty>
              <div className="flex flex-col items-center gap-2 py-4">
                <span className="text-muted-foreground text-sm">
                  No branches found.
                </span>
                {searchQuery.trim() && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleSearchForBranch}
                    disabled={isSearching}
                    className="text-xs"
                  >
                    {isSearching
                      ? "Searching..."
                      : `Search for "${searchQuery.trim()}"`}
                  </Button>
                )}
              </div>
            </CommandEmpty>
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
                          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700 dark:bg-blue-900 dark:text-blue-100">
                            default
                          </span>
                        )}
                        {branch.protected && (
                          <div title="Protected branch">
                            <Shield className="h-3 w-3 text-amber-500 dark:text-amber-400" />
                          </div>
                        )}
                      </div>
                    </CommandItem>
                  );
                })}
            </CommandGroup>
            {/* Show this search button if there is a search query, and there are some results. this is for
            cases when some results do show, just not the exact result the user is looking for */}
            {allowExplicitSearchReqWithResults && (
              <div className="px-2 py-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleSearchForBranch}
                  disabled={isSearching}
                  className="w-full text-xs"
                >
                  {isSearching
                    ? "Searching..."
                    : `Search for "${searchQuery.trim()}"`}
                </Button>
              </div>
            )}
            {branchesHasMore && !allowExplicitSearchReqWithResults && (
              <CommandGroup>
                <CommandItem
                  onSelect={() => {
                    loadMoreBranches();
                  }}
                  disabled={branchesLoadingMore}
                  className="justify-center"
                >
                  {branchesLoadingMore
                    ? "Loading more..."
                    : "Load more branches"}
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
