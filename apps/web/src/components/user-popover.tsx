"use client";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { LogOut } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { useUserStore } from "@/stores/user-store";
import { useUser } from "@/hooks/useUser";

interface UserPopoverProps {
  className?: string;
}

export function UserPopover({ className }: UserPopoverProps) {
  const { user } = useUserStore();
  const { isLoading, error } = useUser();

  const [isLoggingOut, setIsLoggingOut] = useState(false);

  const handleLogout = async () => {
    setIsLoggingOut(true);
    try {
      const response = await fetch("/api/logout", {
        method: "POST",
      });
      if (response.ok) {
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

  if (isLoading || !user) {
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
        <div className="bg-destructive/20 flex h-6 w-6 items-center justify-center rounded-full" />
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
            src={user.avatar_url}
            alt={`${user.login} avatar`}
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
              src={user.avatar_url}
              alt={`${user.login} avatar`}
              className="h-10 w-10 rounded-full"
            />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">
                {user.name ?? user.login}
              </div>
              <div className="text-muted-foreground flex items-center gap-1 text-sm">
                <span className="truncate">@{user.login}</span>
              </div>
            </div>
          </div>

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
