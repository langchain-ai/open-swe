import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { useGitHubInstallations } from "@/hooks/useGitHubInstallations";
import { cn } from "@/lib/utils";
import { Building2, User } from "lucide-react";

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
    isLoading,
    error,
    switchInstallation,
  } = useGitHubInstallations();

  const handleValueChange = (value: string) => {
    switchInstallation(value);
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
        className={cn("min-w-[200px] text-destructive", className)}
      >
        <div className="flex items-center gap-2">
          <GitHubSVG />
          <span>Error loading installations</span>
        </div>
      </Button>
    );
  }

  return (
    <Select
      value={currentInstallation?.id.toString() || ""}
      onValueChange={handleValueChange}
      disabled={disabled || installations.length === 0}
    >
      <SelectTrigger size={size} className={cn("min-w-[200px]", className)}>
        <div className="flex items-center gap-2">
          <GitHubSVG />
          <SelectValue placeholder={placeholder} />
        </div>
      </SelectTrigger>
      <SelectContent>
        {installations.map((installation) => (
          <SelectItem
            key={installation.id}
            value={installation.id.toString()}
          >
            <div className="flex items-center gap-2">
              {getAccountIcon(installation.accountType)}
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

