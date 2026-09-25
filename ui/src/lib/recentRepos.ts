import { create } from "zustand"
import { persist } from "zustand/middleware"

interface RecentRepos {
  byAccount: Record<string, string[]>
  remember: (account: string, repo: string) => void
}

export const useRecentRepos = create<RecentRepos>()(
  persist(
    (set) => ({
      byAccount: {},
      remember: (account, repo) =>
        set((state) => ({
          byAccount: {
            ...state.byAccount,
            [account]: [
              repo,
              ...(state.byAccount[account] ?? []).filter((id) => id !== repo),
            ].slice(0, 5),
          },
        })),
    }),
    { name: "open-swe-recent-repositories" }
  )
)
