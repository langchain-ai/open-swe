"use client";

import React from "react";

interface GitLabAuthStatusProps {
  isAuthenticated: boolean;
  username?: string;
  baseUrl?: string;
  className?: string;
}

export function GitLabAuthStatus({
  isAuthenticated,
  username,
  baseUrl = "https://gitlab.com",
  className = "",
}: GitLabAuthStatusProps) {
  if (!isAuthenticated) {
    return (
      <div className={`flex items-center gap-2 text-gray-500 ${className}`}>
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
        <span className="text-sm">Not connected to GitLab</span>
      </div>
    );
  }

  const displayUrl = baseUrl.replace(/^https?:\/\//, "");

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex items-center gap-2 text-green-600 dark:text-green-400">
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
          <path d="M23.955 13.587l-1.342-4.135-2.664-8.189a.455.455 0 00-.867 0L16.418 9.45H7.582L4.919 1.263a.455.455 0 00-.867 0L1.388 9.452.046 13.587a.924.924 0 00.331 1.031l11.625 8.445 11.625-8.445a.92.92 0 00.328-1.031z"/>
        </svg>
        <span className="text-sm font-medium">
          Connected as <span className="font-bold">{username}</span>
        </span>
      </div>
      {baseUrl !== "https://gitlab.com" && (
        <span className="text-xs text-gray-500 dark:text-gray-400">
          ({displayUrl})
        </span>
      )}
    </div>
  );
}
