"use client";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { Building2, LogOut, User, ChevronDown } from "lucide-react";
import { GitHubSVG } from "@/components/icons/github";
import { cn } from "@/lib/utils";
import { useState } from "react";

interface UserPopoverProps {
  className?: string;
}

export function UserPopover({ className }: UserPopoverProps) {
  const {
    installations,
    currentInstallation,
    installationsLoading: isLoading,
    installationsError: error,
    switchInstallation,
  } = useGitHubAppProvider();
  
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  const GITHUB_APP_INSTALLED_KEY = "github_app_installed";

  const handleLogout = async () => {
    setIsLoggingOut(true);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
      });
      if (response.ok) {
        localStorage.removeItem(GITHUB_APP_INSTALLED_KEY);
        window.location.href = "/";
      } else {
        console.error("Logout failed");
      }
    } catch (error) {
      console.error("Error during logout:", error);
    } finally {
      setIsLoggingOut(false);
    }
  };

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

  // Show loading state
  if (isLoading || !currentInstallation) {
    return (
      <Button
        variant="ghost"
        size="sm"
        disabled
        className={cn("h-8 w-8 rounded-full p-0", className)}
      >
        <div className="h-6 w-6 rounded-full bg-muted animate-pulse" />
      </Button>
    );
  }

  // Show error state
  if (error) {
    return (
      <Button
        variant="ghost"
        size="sm"
        disabled
        className={cn("h-8 w-8 rounded-full p-0", className)}
      >
        <div className="h-6 w-6 rounded-full bg-destructive/20 flex items-center justify-center">
          <GitHubSVG className="h-3 w-3 text-destructive" />
        </div>
      </Button>
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "h-8 w-8 rounded-full p-0 hover:bg-accent",
            className
          )}
        >
          <img
            src={currentInstallation.avatarUrl}
            alt={`${currentInstallation.accountName} avatar`}
            className="h-6 w-6 rounded-full"
          />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="end">
        <div className="p-4">
          {/* Current User Display */}
          <div className="flex items-center gap-3 mb-4">
            <img
              src={currentInstallation.avatarUrl}
              alt={`${currentInstallation.accountName} avatar`}
              className="h-10 w-10 rounded-full"
            />
            <div className="flex-1 min-w-0">
              <div className="font-medium truncate">
                {currentInstallation.accountName}
              </div>
              <div className="text-sm text-muted-foreground flex items-center gap-1">
                {getAccountIcon(currentInstallation.accountType)}
                <span className="capitalize">
                  {currentInstallation.accountType.toLowerCase()}
                </span>
              </div>
            </div>
          </div>

          {/* Installation Switcher */}
          {installations.length > 1 && (
            <>
              <div className="space-y-2 mb-4">
                <label className="text-sm font-medium">Switch Account</label>
                <Select
                  value={currentInstallation.id.toString()}
                  onValueChange={handleValueChange}
                >
                  <SelectTrigger className="w-full">
                    <div className="flex items-center gap-2 flex-1">
                      <img
                        src={currentInstallation.avatarUrl}
                        alt={`${currentInstallation.accountName} avatar`}
                        className="h-4 w-4 rounded-full"
                      />
                      <span className="truncate">
                        {currentInstallation.accountName}
                      </span>
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
                          {getAccountIcon(installation.accountType)}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Separator className="mb-4" />
            </>
          )}

          {/* Logout Button */}
          <Button
            variant="ghost"
            className="w-full justify-start text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-950/50"
            onClick={handleLogout}
            disabled={isLoggingOut}
          >
            <LogOut className="h-4 w-4 mr-2" />
            {isLoggingOut ? "Signing out..." : "Sign out"}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}