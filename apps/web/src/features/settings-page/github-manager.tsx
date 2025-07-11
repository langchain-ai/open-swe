"use client";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { RefreshCw, GitBranch, Lock, Globe, ExternalLink } from "lucide-react";
import { useState } from "react";

interface Repository {
  name: string;
  fullName: string;
  description: string | null;
  isPrivate: boolean;
  defaultBranch: string;
  updatedAt: string;
}

export function GitHubManager() {
  const [isRefreshing, setIsRefreshing] = useState(false);

  // GitHub repositories mock data
  const repositories: Repository[] = [
    {
      name: "chat-langchain",
      fullName: "langchain-ai/chat-langchain",
      description: null,
      isPrivate: false,
      defaultBranch: "master",
      updatedAt: "2 days ago",
    },
    {
      name: "langsmith-sdk",
      fullName: "langchain-ai/langsmith-sdk",
      description: "LangSmith Client SDK implementations",
      isPrivate: false,
      defaultBranch: "main",
      updatedAt: "1 week ago",
    },
    {
      name: "langgraph",
      fullName: "langchain-ai/langgraph",
      description: "Build resilient language agents as graphs.",
      isPrivate: false,
      defaultBranch: "main",
      updatedAt: "3 days ago",
    },
    {
      name: "open-swe",
      fullName: "langchain-ai/open-swe",
      description: null,
      isPrivate: false,
      defaultBranch: "main",
      updatedAt: "1 day ago",
    },
    {
      name: "open-swe-dev",
      fullName: "langchain-ai/open-swe-dev",
      description:
        "Development repo. Should be used for testing open-swe agent",
      isPrivate: true,
      defaultBranch: "main",
      updatedAt: "5 hours ago",
    },
  ];

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await new Promise((resolve) => setTimeout(resolve, 1000));
    setIsRefreshing(false);
  };

  return (
    <div className="space-y-8">
      {/* Repository Access Section */}
      <Card className="bg-white shadow-sm">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-xl">Your Repositories</CardTitle>
              <CardDescription>
                Repositories you have access to through your GitHub integration
              </CardDescription>
            </div>
            <div className="flex items-center gap-3">
              <Badge
                variant="secondary"
                className="bg-gray-100 font-mono"
              >
                {repositories.length} repositories
              </Badge>
              <Button
                onClick={handleRefresh}
                disabled={isRefreshing}
                variant="outline"
                size="sm"
                className="bg-white"
              >
                <RefreshCw
                  className={`mr-2 h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`}
                />
                Refresh
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {repositories.map((repo, index) => (
            <div key={repo.fullName}>
              <div className="flex items-start justify-between rounded-lg border border-gray-100 p-4 transition-colors hover:border-gray-200">
                <div className="min-w-0 flex-1">
                  <div className="mb-2 flex items-center gap-2">
                    <h3 className="cursor-pointer font-mono font-semibold text-blue-600 hover:text-blue-700">
                      {repo.fullName}
                    </h3>
                    <Badge
                      variant={repo.isPrivate ? "secondary" : "outline"}
                      className="text-xs"
                    >
                      {repo.isPrivate ? (
                        <>
                          <Lock className="mr-1 h-3 w-3" />
                          Private
                        </>
                      ) : (
                        <>
                          <Globe className="mr-1 h-3 w-3" />
                          Public
                        </>
                      )}
                    </Badge>
                  </div>

                  {repo.description && (
                    <p className="mb-3 text-sm text-gray-600">
                      {repo.description}
                    </p>
                  )}

                  <div className="flex items-center gap-4 text-sm text-gray-500">
                    <div className="flex items-center gap-1">
                      <GitBranch className="h-3 w-3" />
                      <span className="font-mono">
                        Default: {repo.defaultBranch}
                      </span>
                    </div>
                    <span>Updated {repo.updatedAt}</span>
                  </div>
                </div>

                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-4"
                >
                  <ExternalLink className="h-4 w-4" />
                </Button>
              </div>
              {index < repositories.length - 1 && (
                <Separator className="my-2" />
              )}
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-xl">GitHub App Management</CardTitle>
          <CardDescription>
            Manage your GitHub App installation and permissions
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4">
            <div>
              <h3 className="mb-1 font-semibold text-gray-900">
                GitHub App Installation
              </h3>
              <p className="text-sm text-gray-600">
                You can manage your GitHub App installation, including adding or
                removing repositories, through GitHub.
              </p>
            </div>
            <Button
              variant="outline"
              className="bg-white"
            >
              <ExternalLink className="mr-2 h-4 w-4" />
              Manage on GitHub
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
``}
