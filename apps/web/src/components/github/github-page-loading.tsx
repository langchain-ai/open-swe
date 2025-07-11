import React from "react";

export function GithubPageLoading() {
  return (
    <div className="container mx-auto px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-3xl font-bold">GitHub Repository Access</h1>
        <div className="h-8 w-48 animate-pulse rounded bg-gray-200 dark:bg-gray-700"></div>
      </div>
      <div className="mb-6 rounded-lg bg-white p-6 shadow-md dark:bg-gray-900">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">Your Repositories</h2>
          <div className="h-10 w-24 animate-pulse rounded bg-gray-200 dark:bg-gray-700"></div>
        </div>
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, index) => (
            <div
              key={index}
              className="py-4"
            >
              <div className="mb-2 h-4 w-3/4 animate-pulse rounded bg-gray-200 dark:bg-gray-700"></div>
              <div className="mb-2 h-3 w-1/2 animate-pulse rounded bg-gray-200 dark:bg-gray-700"></div>
              <div className="h-3 w-1/4 animate-pulse rounded bg-gray-200 dark:bg-gray-700"></div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
