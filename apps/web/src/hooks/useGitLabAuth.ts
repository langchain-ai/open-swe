"use client";

import { useState, useEffect } from "react";

interface GitLabAuthState {
  isAuthenticated: boolean;
  loading: boolean;
}

export function useGitLabAuth(): GitLabAuthState {
  const [authState, setAuthState] = useState<GitLabAuthState>({
    isAuthenticated: false,
    loading: true,
  });

  useEffect(() => {
    const checkAuth = async () => {
      try {
        // Use the auth status API endpoint to check GitLab auth
        const response = await fetch("/api/auth/status");
        const data = await response.json();

        setAuthState({
          isAuthenticated: data.authenticated && data.provider === "gitlab",
          loading: false,
        });
      } catch (error) {
        console.error("Error checking GitLab auth:", error);
        setAuthState({
          isAuthenticated: false,
          loading: false,
        });
      }
    };

    checkAuth();
  }, []);

  return authState;
}
