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
