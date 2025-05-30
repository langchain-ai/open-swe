"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { isAuthenticated, clearGitHubToken } from "@/lib/auth";

export function GitHubOAuthButton() {
  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    try {
      const authenticated = await isAuthenticated();
      setIsAuth(authenticated);
    } catch (error) {
      console.error("Error checking auth status:", error);
      setIsAuth(false);
    }
  };

  const handleLogin = () => {
    setIsLoading(true);
    window.location.href = "/api/auth/github/login";
  };

  const handleLogout = async () => {
    setIsLoading(true);
    try {
      await clearGitHubToken();
      setIsAuth(false);
    } catch (error) {
      console.error("Error during logout:", error);
    } finally {
      setIsLoading(false);
    }
  };

  if (isAuth === null) {
    return (
      <Button
        variant="outline"
        disabled
      >
        <GitHubSVG
          width="16"
          height="16"
        />
        Checking...
      </Button>
    );
  }

  if (isAuth) {
    return (
      <Button
        variant="outline"
        onClick={handleLogout}
        disabled={isLoading}
      >
        <GitHubSVG
          width="16"
          height="16"
        />
        {isLoading ? "Disconnecting..." : "Disconnect GitHub"}
      </Button>
    );
  }

  return (
    <Button
      onClick={handleLogin}
      disabled={isLoading}
    >
      <GitHubSVG
        width="16"
        height="16"
      />
      {isLoading ? "Connecting..." : "Connect GitHub"}
    </Button>
  );
}
