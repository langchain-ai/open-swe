class AppDistribution {
  get isDesktop(): boolean {
    return typeof window !== "undefined" && Boolean(window.openSweDesktop)
  }

  get name(): string {
    return "Open SWE"
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
