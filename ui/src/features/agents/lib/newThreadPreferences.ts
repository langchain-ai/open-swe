import type { DesktopWorkspaceMode } from "@/desktop"

class NewThreadPreferences {
  private readonly key = "open-swe.new-thread"

  private read(): Record<string, unknown> {
    if (typeof window === "undefined") return {}
    try {
      const value: unknown = JSON.parse(
        window.localStorage.getItem(this.key) ?? "{}"
      )
      return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {}
    } catch (error) {
      console.warn("Could not read new-thread preferences", error)
      return {}
    }
  }

  private write(values: Record<string, unknown>) {
    try {
      window.localStorage.setItem(
        this.key,
        JSON.stringify({ ...this.read(), ...values })
      )
    } catch (error) {
      console.warn("Could not save new-thread preferences", error)
    }
  }

  repo(login: string): string | null | undefined {
    const value = this.read()[`repo:${login}`]
    return value === null || typeof value === "string" ? value : undefined
  }

  selectRepo(login: string, repo: string | null) {
    this.write({ [`repo:${login}`]: repo })
  }

  branch(cwd: string): string | null {
    const value = this.read()[`branch:${cwd}`]
    return typeof value === "string" ? value : null
  }

  selectBranch(cwd: string, branch: string) {
    this.write({ [`branch:${cwd}`]: branch })
  }

  workspaceMode(): DesktopWorkspaceMode {
    return this.read().workspaceMode === "worktree" ? "worktree" : "local"
  }

  selectWorkspaceMode(workspaceMode: DesktopWorkspaceMode) {
    this.write({ workspaceMode })
  }
}

export const newThreadPreferences = new NewThreadPreferences()
