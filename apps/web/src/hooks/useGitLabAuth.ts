"use client";

import { useState, useEffect } from "react";
import { GITLAB_TOKEN_COOKIE } from "@openswe/shared/constants";

interface GitLabAuthState {
  isAuthenticated: boolean;
  username?: string;
  userId?: string;
  baseUrl?: string;
  loading: boolean;
}

export function useGitLabAuth(): GitLabAuthState {
  const [authState, setAuthState] = useState<GitLabAuthState>({
    isAuthenticated: false,
    loading: true,
  });

  useEffect(() => {
    // Check if GitLab token exists in cookies
    const checkAuth = async () => {
      try {
        // Get cookies from document
        const cookies = document.cookie.split(";").reduce((acc, cookie) => {
          const [key, value] = cookie.trim().split("=");
          acc[key] = value;
          return acc;
        }, {} as Record<string, string>);

        const hasToken = !!cookies[GITLAB_TOKEN_COOKIE];
        const username = cookies["gitlab_user_login"];
        const userId = cookies["gitlab_user_id"];
        const baseUrl = cookies["x-gitlab-base-url"] || "https://gitlab.com";

        setAuthState({
          isAuthenticated: hasToken,
          username: username ? decodeURIComponent(username) : undefined,
          userId: userId ? decodeURIComponent(userId) : undefined,
          baseUrl: baseUrl ? decodeURIComponent(baseUrl) : "https://gitlab.com",
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
