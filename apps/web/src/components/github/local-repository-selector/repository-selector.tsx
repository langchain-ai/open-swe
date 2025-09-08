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
import { Check, ChevronsUpDown, Folder } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import type { TargetRepository } from "@openswe/shared/open-swe/types";
import { useLocalRepositories } from "@/hooks/useLocalRepositories";
import { useQueryState } from "nuqs";

interface RepositorySelectorProps {
  disabled?: boolean;
  placeholder?: string;
  buttonClassName?: string;
  chatStarted?: boolean;
  streamTargetRepository?: TargetRepository;
}
export function RepositorySelector({
  disabled = false,
  placeholder = "Select a repository...",
  buttonClassName,
  chatStarted = false,
  streamTargetRepository,
}: RepositorySelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const {
    repositories: localRepos,
    isLoading,
    error,
  } = useLocalRepositories(search);
  const [selectedRepository, setSelectedRepository] = useQueryState("repo");

  const handleSelect = (repoPath: string) => {
    setSelectedRepository(repoPath);
    setOpen(false);
  };

  const selectedValue = selectedRepository || undefined;
  const displayValue =
    chatStarted && streamTargetRepository
      ? streamTargetRepository.repo
      : selectedValue;

  if (isLoading) {
    return (
      <Button
        variant="outline"
        disabled
        className={cn(buttonClassName)}
        size="sm"
      >
        <span>Loading repositories...</span>
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
        <span>Error loading repositories</span>
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
          <Folder />
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
            <Folder />
            <span className="truncate text-left">
              {selectedValue || placeholder}
            </span>
          </div>
          <ChevronsUpDown className="h-4 w-4 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[340px] p-0">
        <Command>
          <CommandInput
            placeholder="Search repositories..."
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>No repositories found.</CommandEmpty>
            <CommandGroup>
              {localRepos.map((repo) => {
                const key = repo.path;
                const isSelected = selectedValue === key;
                return (
                  <CommandItem
                    key={key}
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
                      <span className="font-medium">{repo.name}</span>
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
