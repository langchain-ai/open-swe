"use client";

import React from "react";

interface GitLabAuthButtonProps {
  className?: string;
}

export function GitLabAuthButton({ className = "" }: GitLabAuthButtonProps) {
  const handleLogin = () => {
    window.location.href = "/api/auth/gitlab/login";
  };

  return (
    <button
      onClick={handleLogin}
      className={`
        flex items-center gap-2 px-4 py-2
        bg-orange-600 hover:bg-orange-700
        text-white font-medium rounded-lg
        transition-colors
        ${className}
      `}
    >
      <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
        <path d="M23.955 13.587l-1.342-4.135-2.664-8.189a.455.455 0 00-.867 0L16.418 9.45H7.582L4.919 1.263a.455.455 0 00-.867 0L1.388 9.452.046 13.587a.924.924 0 00.331 1.031l11.625 8.445 11.625-8.445a.92.92 0 00.328-1.031z"/>
      </svg>
      <span>Connect GitLab</span>
    </button>
  );
}
