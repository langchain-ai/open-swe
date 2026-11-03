class ComposerRepoSelection {
  private listeners = new Set<() => void>()

  key(userId: string) {
    return `open-swe.agents.last-repo:${userId}`
  }

  read(userId: string): string | null | undefined {
    if (!userId || typeof window === "undefined") return undefined
    try {
      const stored = window.localStorage.getItem(this.key(userId))
      if (stored === null) return undefined
      const value: unknown = JSON.parse(stored)
      return value === null || typeof value === "string" ? value : undefined
    } catch (error) {
      console.warn("Could not read the last selected repository", error)
      return undefined
    }
  }

  write(userId: string, repo: string | null) {
    if (!userId) return
    try {
      window.localStorage.setItem(this.key(userId), JSON.stringify(repo))
    } catch (error) {
      console.warn("Could not save the last selected repository", error)
    }
    this.listeners.forEach((listener) => listener())
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    window.addEventListener("storage", listener)
    return () => {
      this.listeners.delete(listener)
      window.removeEventListener("storage", listener)
    }
  }
}

export const composerRepoSelection = new ComposerRepoSelection()
