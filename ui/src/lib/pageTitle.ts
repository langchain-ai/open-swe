class AppDistribution {
  get agentWorkspace(): boolean {
    return (
      typeof window !== "undefined" &&
      window.openSweDesktop?.appVariant === "agent-workspace"
    )
  }

  get name(): string {
    return this.agentWorkspace ? "Agent Workspace" : "Open SWE"
  }

  get loginPath(): string {
    return this.agentWorkspace ? "/auth/langsmith/login" : "/auth/login"
  }
}

export const appDistribution = new AppDistribution()

/**
 * Document title for a dashboard page: the page name, a middle dot, the app.
 * The root page passes no name and gets the bare app name.
 */
export function pageTitle(page?: string | null): string {
  return page ? `${page} · ${appDistribution.name}` : appDistribution.name
}
