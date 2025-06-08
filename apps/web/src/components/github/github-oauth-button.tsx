"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";

export function GitHubOAuthButton() {
  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setIsAuth(data.authenticated);
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
      const response = await fetch("/api/auth/logout", {
        method: "POST",
      });
      if (response.ok) {
        setIsAuth(false);
      } else {
        console.error("Logout failed");
      }
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
        className="text-xs"
        size="sm"
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
        className="text-xs"
        size="sm"
      >
        <GitHubSVG />
        {isLoading ? "Disconnecting..." : "Disconnect GitHub"}
      </Button>
    );
  }

  return (
    <Button
      variant="outline"
      onClick={handleLogin}
      disabled={isLoading}
      className="text-xs"
      size="sm"
    >
      <GitHubSVG />
      {isLoading ? "Connecting..." : "Connect GitHub"}
    </Button>
  );
}
