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
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { Building2, LogOut, User } from "lucide-react";
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

  if (isLoading || !currentInstallation) {
    return (
      <Button
        variant="ghost"
        size="sm"
        disabled
        className={cn("h-8 w-8 rounded-full p-0", className)}
      >
        <div className="bg-muted h-6 w-6 animate-pulse rounded-full" />
      </Button>
    );
  }

  if (error) {
    return (
      <Button
        variant="ghost"
        size="sm"
        disabled
        className={cn("h-8 w-8 rounded-full p-0", className)}
      >
        <div className="bg-destructive/20 flex h-6 w-6 items-center justify-center rounded-full">
          <GitHubSVG className="text-destructive h-3 w-3" />
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
          className={cn("hover:bg-accent h-8 w-8 rounded-full p-0", className)}
        >
          <img
            src={currentInstallation.avatarUrl}
            alt={`${currentInstallation.accountName} avatar`}
            className="h-6 w-6 rounded-full"
          />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-80 p-0"
        align="end"
      >
        <div className="p-4">
          <div className="mb-4 flex items-center gap-3">
            <img
              src={currentInstallation.avatarUrl}
              alt={`${currentInstallation.accountName} avatar`}
              className="h-10 w-10 rounded-full"
            />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">
                {currentInstallation.accountName}
              </div>
              <div className="text-muted-foreground flex items-center gap-1 text-sm">
                {getAccountIcon(currentInstallation.accountType)}
                <span className="capitalize">
                  {currentInstallation.accountType.toLowerCase()}
                </span>
              </div>
            </div>
          </div>

          {installations.length > 1 && (
            <>
              <div className="mb-4 space-y-2">
                <label className="text-sm font-medium">Switch Account</label>
                <Select
                  value={currentInstallation.id.toString()}
                  onValueChange={handleValueChange}
                >
                  <SelectTrigger className="w-full">
                    <div className="flex flex-1 items-center gap-2">
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

          <Button
            variant="ghost"
            className="w-full justify-start text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-950/50 dark:hover:text-red-300"
            onClick={handleLogout}
            disabled={isLoggingOut}
          >
            <LogOut className="mr-2 h-4 w-4" />
            {isLoggingOut ? "Signing out..." : "Sign out"}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
