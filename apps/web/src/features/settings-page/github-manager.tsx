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
import {
  RefreshCw,
  GitBranch,
  Lock,
  Globe,
  ExternalLink,
  Building2,
  User,
  Github,
  LogIn,
  Plus,
} from "lucide-react";
import { useGitHubAppProvider } from "@/providers/GitHubApp";
import { InstallationSelector } from "@/components/github/installation-selector";
import { cn } from "@/lib/utils";

export function GitHubManager() {
  const {
    isInstalled,
    isLoading,
    error,
    installations,
    currentInstallation,
    installationsLoading,
    installationsError,
    switchInstallation,
    refreshInstallations,
    repositories,
    repositoriesLoadingMore,
    refreshRepositories,
    loadMoreRepositories,
    repositoriesHasMore,
  } = useGitHubAppProvider();

  const handleRefresh = async () => {
    await Promise.all([refreshRepositories(), refreshInstallations()]);
  };

  const handleInstallApp = () => {
    window.location.href = "/api/github/installation";
  };

  const handleManageOnGitHub = () => {
    const appId = process.env.NEXT_PUBLIC_GITHUB_APP_CLIENT_ID;
    const fallbackInstallationsUrl =
      "https://github.com/settings/installations";
    const applicationUrl = appId
      ? `https://github.com/settings/connections/applications/${appId}`
      : fallbackInstallationsUrl;
    window.open(applicationUrl, "_blank");
  };

  const handleLogin = () => {
    window.location.href = "/api/auth/github/login";
  };

  const isAuthError = (errorMessage: string | null) => {
    return (
      errorMessage?.includes("GitHub access token not found") ||
      errorMessage?.includes("Please authenticate first") ||
      errorMessage?.includes("authentication") ||
      errorMessage?.includes("access token")
    );
  };

  if (isAuthError(installationsError) || isAuthError(error)) {
    return (
      <div className="space-y-8">
        <Card className="bg-card border-border shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-xl">
              <Github className="h-6 w-6" />
              GitHub Authentication Required
            </CardTitle>
            <CardDescription>
              Sign in with GitHub to access your repositories and manage
              installations
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="border-border bg-muted/50 rounded-lg border p-6">
              <div className="mb-6 text-center">
                <Github className="text-muted-foreground mx-auto mb-4 h-16 w-16" />
                <h3 className="text-foreground mb-2 text-lg font-semibold">
                  Connect Your GitHub Account
                </h3>
                <p className="text-muted-foreground mx-auto max-w-md text-sm">
                  To manage your GitHub repositories and app installations, you
                  need to authenticate with GitHub first. This will allow you to
                  select repositories and work with your code.
                </p>
              </div>
              <div className="flex flex-col gap-3">
                <Button
                  onClick={handleLogin}
                  className="w-full"
                  size="lg"
                >
                  <LogIn className="mr-2 h-4 w-4" />
                  Sign in with GitHub
                </Button>
                <Button
                  onClick={handleRefresh}
                  variant="outline"
                  className="w-full"
                >
                  <RefreshCw className="mr-2 h-4 w-4" />
                  Check Authentication Status
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading || installationsLoading) {
    return (
      <div className="space-y-8">
        <Card className="bg-card border-border shadow-sm">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-xl">GitHub Integration</CardTitle>
                <CardDescription>Loading your GitHub setup...</CardDescription>
              </div>
              <div className="bg-muted h-10 w-24 animate-pulse rounded"></div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="bg-muted h-20 animate-pulse rounded"
              ></div>
            ))}
          </CardContent>
        </Card>
      </div>
    );
  }

  if (
    (error && !isAuthError(error)) ||
    (installationsError && !isAuthError(installationsError))
  ) {
    return (
      <div className="space-y-8">
        <Card className="bg-card border-border shadow-sm">
          <CardHeader>
            <CardTitle className="text-destructive text-xl">
              GitHub Integration Error
            </CardTitle>
            <CardDescription>{error || installationsError}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex gap-2">
              <Button
                onClick={handleRefresh}
                variant="outline"
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                Try Again
              </Button>
              <Button
                onClick={handleLogin}
                variant="default"
              >
                <LogIn className="mr-2 h-4 w-4" />
                Re-authenticate
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!isInstalled) {
    return (
      <div className="space-y-8">
        <Card className="bg-card border-border shadow-sm">
          <CardHeader>
            <CardTitle className="text-xl">GitHub App Installation</CardTitle>
            <CardDescription>
              Install the GitHub App to connect your repositories
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="border-border bg-muted/50 rounded-lg border p-6">
              <div className="mb-4">
                <h3 className="text-foreground mb-2 font-semibold">
                  Get Started with GitHub Integration
                </h3>
                <p className="text-muted-foreground text-sm">
                  To access your GitHub repositories, you need to install our
                  GitHub App and grant it access to the repositories you want to
                  use.
                </p>
              </div>
              <Button
                onClick={handleInstallApp}
                className="w-full"
              >
                Install GitHub App
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <Card className="bg-card border-border shadow-sm">
        <CardHeader>
          <CardTitle className="text-xl">
            GitHub{" "}
            {currentInstallation?.accountType === "Organization"
              ? "Organization"
              : "User"}
          </CardTitle>
          <CardDescription>
            Manage your GitHub App installations and switch between
            organizations
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="border-border bg-muted/50 flex items-center justify-between rounded-lg border p-4">
            <div className="flex-1">
              <h3 className="text-foreground mb-1 font-semibold">
                Current{" "}
                {currentInstallation?.accountType === "Organization"
                  ? "Organization"
                  : "User"}
              </h3>
              <p className="text-muted-foreground text-sm">
                Select which GitHub organization or user account to work with
              </p>
            </div>
            <div className="flex items-center gap-3">
              <InstallationSelector
                size="default"
                className="min-w-[250px]"
              />
              <Button
                onClick={handleInstallApp}
                variant="outline"
                size="sm"
                title="Add new organization"
              >
                <Plus className="h-4 w-4" />
              </Button>
              <Button
                onClick={refreshInstallations}
                variant="outline"
                size="sm"
                disabled={installationsLoading}
              >
                <RefreshCw
                  className={cn(
                    "h-4 w-4",
                    installationsLoading && "animate-spin",
                  )}
                />
              </Button>
            </div>
          </div>

          {currentInstallation && (
            <div className="border-border flex items-center gap-4 rounded-lg border p-4">
              <img
                src={currentInstallation.avatarUrl}
                alt={`${currentInstallation.accountName} avatar`}
                className="h-12 w-12 rounded-full"
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h4 className="text-foreground font-semibold">
                    {currentInstallation.accountName}
                  </h4>
                  <Badge
                    variant="outline"
                    className="text-xs"
                  >
                    {currentInstallation.accountType === "Organization" ? (
                      <>
                        <Building2 className="mr-1 h-3 w-3" />
                        Organization
                      </>
                    ) : (
                      <>
                        <User className="mr-1 h-3 w-3" />
                        Personal
                      </>
                    )}
                  </Badge>
                </div>
                <p className="text-muted-foreground text-sm">
                  {repositories.length} repositories accessible
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="bg-card border-border shadow-sm">
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
                className="font-mono"
              >
                {repositories.length} repositories
              </Badge>
              <Button
                onClick={handleRefresh}
                disabled={isLoading || repositoriesLoadingMore}
                variant="outline"
                size="sm"
              >
                <RefreshCw
                  className={cn(
                    "mr-2 h-4 w-4",
                    (isLoading || repositoriesLoadingMore) && "animate-spin",
                  )}
                />
                Refresh
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {repositories.length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-muted-foreground">
                No repositories found. Make sure you've granted access to at
                least one repository for the selected organization.
              </p>
            </div>
          ) : (
            <>
              {repositories.map((repo, index) => (
                <div key={repo.id}>
                  <div className="border-border hover:bg-muted/50 flex items-start justify-between rounded-lg border p-4 transition-colors">
                    <div className="min-w-0 flex-1">
                      <div className="mb-2 flex items-center gap-2">
                        <h3 className="text-primary hover:text-primary/80 cursor-pointer font-mono font-semibold">
                          {repo.full_name}
                        </h3>
                        <Badge
                          variant={repo.private ? "secondary" : "outline"}
                          className="text-xs"
                        >
                          {repo.private ? (
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
                        <p className="text-muted-foreground mb-3 text-sm">
                          {repo.description}
                        </p>
                      )}

                      <div className="text-muted-foreground flex items-center gap-4 text-sm">
                        <div className="flex items-center gap-1">
                          <GitBranch className="h-3 w-3" />
                          <span className="font-mono">
                            Default: {repo.default_branch}
                          </span>
                        </div>
                      </div>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-4"
                      onClick={() => window.open(repo.html_url, "_blank")}
                    >
                      <ExternalLink className="h-4 w-4" />
                    </Button>
                  </div>
                  {index < repositories.length - 1 && (
                    <Separator className="my-2" />
                  )}
                </div>
              ))}

              {repositoriesHasMore && (
                <div className="pt-4 text-center">
                  <Button
                    onClick={loadMoreRepositories}
                    disabled={repositoriesLoadingMore}
                    variant="outline"
                  >
                    {repositoriesLoadingMore ? (
                      <>
                        <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                        Loading...
                      </>
                    ) : (
                      "Load More Repositories"
                    )}
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card className="bg-card border-border shadow-sm">
        <CardHeader>
          <CardTitle className="text-xl">GitHub App Management</CardTitle>
          <CardDescription>
            Manage your GitHub App installation and permissions
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border-border bg-muted/50 flex items-center justify-between rounded-lg border p-4">
            <div>
              <h3 className="text-foreground mb-1 font-semibold">
                GitHub App Installation
              </h3>
              <p className="text-muted-foreground text-sm">
                You can manage your GitHub App installation, including adding or
                removing repositories, through GitHub.
              </p>
            </div>
            <Button
              onClick={handleManageOnGitHub}
              variant="outline"
            >
              <ExternalLink className="mr-2 h-4 w-4" />
              Manage on GitHub
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
