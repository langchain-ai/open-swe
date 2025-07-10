import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { cn } from "@/lib/utils";
import { Building2, User } from "lucide-react";
import type { Installation } from "@/hooks/useGitHubInstallations";

interface InstallationSelectorProps {
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  size?: "sm" | "default";
}

export function InstallationSelector({
  disabled = false,
  placeholder = "Select organization/user...",
  className,
  size = "sm",
}: InstallationSelectorProps) {
  const {
    installations,
    currentInstallation,
    installationsLoading: isLoading,
    installationsError: error,
    switchInstallation,
  } = useGitHubAppProvider();

  const handleValueChange = async (value: string) => {
    await switchInstallation(value);
  };

  const getAccountIcon = (accountType: "User" | "Organization") => {
    return accountType === "Organization" ? (
      <Building2 className="h-4 w-4" />
    ) : (
      <User className="h-4 w-4" />
    );
  };

  if (isLoading) {
    return (
      <Button
        variant="outline"
        disabled
        size={size}
        className={cn("min-w-[200px]", className)}
      >
        <div className="flex items-center gap-2">
          <GitHubSVG />
          <span>Loading...</span>
        </div>
      </Button>
    );
  }

  if (error) {
    return (
      <Button
        variant="outline"
        disabled
        size={size}
        className={cn("text-destructive min-w-[200px]", className)}
      >
        <div className="flex items-center gap-2">
          <GitHubSVG />
          <span>Error loading installations</span>
        </div>
      </Button>
    );
  }

  if (installations.length === 0) {
    return (
      <Button
        variant="outline"
        disabled
        size={size}
        className={cn("text-muted-foreground min-w-[200px]", className)}
      >
        <div className="flex items-center gap-2">
          <GitHubSVG />
          <span>No installations found</span>
        </div>
      </Button>
    );
  }

  return (
    <Select
      value={currentInstallation?.id.toString() || ""}
      onValueChange={handleValueChange}
      disabled={disabled}
    >
      <SelectTrigger
        size={size}
        className={cn("min-w-[200px]", className)}
      >
        <div className="flex items-center gap-2">
          {currentInstallation ? (
            <div className="flex items-center gap-2">
              <img
                src={currentInstallation.avatarUrl}
                alt={`${currentInstallation.accountName} avatar`}
                className="h-4 w-4 rounded-full"
              />
              <span className="truncate">
                {currentInstallation.accountName}
              </span>
            </div>
          ) : (
            <SelectValue placeholder={placeholder} />
          )}
        </div>
      </SelectTrigger>
      <SelectContent>
        {installations.map((installation) => (
          <SelectItem
            key={installation.id}
            value={installation.id.toString()}
          >
            <div className="flex items-center gap-2">
              <img
                src={installation.avatarUrl}
                alt={`${installation.accountName} avatar`}
                className="h-4 w-4 rounded-full"
              />
              <span>{installation.accountName}</span>
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
