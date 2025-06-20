"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { LogOut } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

const GITHUB_APP_INSTALL_URL = "/api/github/installation";
const GITHUB_LOGIN_URL = "/api/auth/github/login";
const GITHUB_LOGOUT_URL = "/api/auth/logout";

interface GitHubUser {
  login: string;
  avatar_url: string;
  html_url: string;
  name?: string;
  email?: string;
}

export default function AuthStatus() {
  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isInstalled, setIsInstalled] = useState<boolean | null>(null);
  const [isInstallLoading, setIsInstallLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<GitHubUser | null>(null);
  const [userLoading, setUserLoading] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    if (isAuth) {
      checkAppInstallation();
      fetchGitHubUser();
    } else {
      setIsInstalled(null);
      setUser(null);
    }
  }, [isAuth]);

  const checkAuthStatus = async () => {
    setIsLoading(true);
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setIsAuth(data.authenticated);
    } catch (err) {
      setIsAuth(false);
    } finally {
      setIsLoading(false);
    }
  };

  const checkAppInstallation = async () => {
    setIsInstallLoading(true);
    try {
      const response = await fetch("/api/github/repositories");
      if (response.ok) {
        setIsInstalled(true);
      } else {
        const data = await response.json();
        if (data.error && data.error.includes("installation")) {
          setIsInstalled(false);
        } else {
          setError(data.error || "Unknown error");
        }
      }
    } catch (err) {
      setError("Failed to check GitHub App installation status");
    } finally {
      setIsInstallLoading(false);
    }
  };

  const fetchGitHubUser = async () => {
    setUserLoading(true);
    try {
      const response = await fetch("/api/auth/user");
      if (response.ok) {
        const data = await response.json();
        setUser(data.user);
      } else {
        setUser(null);
      }
    } catch {
      setUser(null);
    } finally {
      setUserLoading(false);
    }
  };

  const handleLogin = () => {
    window.location.href = GITHUB_LOGIN_URL;
  };

  const handleLogout = async () => {
    setIsLoading(true);
    try {
      await fetch(GITHUB_LOGOUT_URL, { method: "POST" });
      window.location.href = "/";
    } catch {
      setIsLoading(false);
    }
  };

  const handleInstall = () => {
    window.location.href = GITHUB_APP_INSTALL_URL;
  };

  return (
    <div className="flex items-center gap-2">
      {isLoading ? (
        <Button
          variant="outline"
          size="sm"
          disabled
        >
          <GitHubSVG
            width="16"
            height="16"
          />{" "}
          Checking...
        </Button>
      ) : isAuth ? (
        <Popover
          open={popoverOpen}
          onOpenChange={setPopoverOpen}
        >
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="flex items-center gap-2"
              onClick={() => setPopoverOpen((open) => !open)}
            >
              {userLoading ? (
                <span className="h-5 w-5 animate-pulse rounded-full bg-gray-200" />
              ) : user && user.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt={user.login}
                  className="h-5 w-5 rounded-full border border-gray-300"
                />
              ) : (
                <GitHubSVG
                  width="16"
                  height="16"
                />
              )}
              {user && user.login ? (
                <span className="font-mono text-xs">{user.login}</span>
              ) : (
                <span className="font-mono text-xs">GitHub User</span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            className="w-64 p-3"
          >
            <div className="flex flex-col gap-2">
              {userLoading ? (
                <div className="flex items-center gap-2">
                  <span className="h-8 w-8 animate-pulse rounded-full bg-gray-200" />
                  <div className="flex-1">
                    <div className="mb-1 h-3 w-20 animate-pulse rounded bg-gray-200" />
                    <div className="h-3 w-32 animate-pulse rounded bg-gray-100" />
                  </div>
                </div>
              ) : user ? (
                <div className="mb-2 flex items-center gap-3">
                  <img
                    src={user.avatar_url}
                    alt={user.login}
                    className="h-8 w-8 rounded-full border border-gray-300"
                  />
                  <div>
                    <div className="font-semibold">
                      {user.name || user.login}
                    </div>
                    <div className="text-xs text-gray-500">
                      {user.email || user.login}
                    </div>
                  </div>
                </div>
              ) : null}
              {isInstallLoading ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled
                >
                  <GitHubSVG
                    width="16"
                    height="16"
                  />{" "}
                  Checking App...
                </Button>
              ) : isInstalled === false ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleInstall}
                  disabled={isInstallLoading}
                  className="w-full"
                >
                  <GitHubSVG
                    width="16"
                    height="16"
                  />{" "}
                  Install GitHub App
                </Button>
              ) : isInstalled === true ? (
                <Badge
                  variant="secondary"
                  className="flex w-full items-center justify-center gap-1"
                >
                  <GitHubSVG
                    width="14"
                    height="14"
                  />{" "}
                  App Installed
                </Badge>
              ) : null}
              <Button
                variant="outline"
                size="sm"
                onClick={handleLogout}
                disabled={isLoading}
                className="flex w-full items-center gap-2"
              >
                <LogOut className="size-4" /> Logout
              </Button>
            </div>
          </PopoverContent>
        </Popover>
      ) : (
        <Button
          variant="outline"
          onClick={handleLogin}
          disabled={isLoading}
          size="sm"
        >
          <GitHubSVG
            width="16"
            height="16"
          />{" "}
          Connect GitHub
        </Button>
      )}
      {error && <span className="ml-2 text-xs text-red-500">{error}</span>}
    </div>
  );
}
