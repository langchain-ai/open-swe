import type { RepositoryUsage } from "./api"

export function prioritizeRepositories<T>(
  repositories: ReadonlyArray<T>,
  usage: ReadonlyArray<RepositoryUsage> = [],
  name: (repository: T) => string
): Array<T> {
  const available = new Set(
    repositories.map((repo) => name(repo).toLowerCase())
  )
  const favorites = [...usage]
    .filter((entry) => available.has(entry.repo.toLowerCase()))
    .sort(
      (a, b) =>
        b.use_count - a.use_count ||
        Date.parse(b.last_used_at) - Date.parse(a.last_used_at) ||
        a.repo.localeCompare(b.repo)
    )
    .slice(0, 4)
  const ranks = new Map(
    favorites.map((entry, index) => [entry.repo.toLowerCase(), index])
  )
  return [...repositories].sort(
    (a, b) =>
      (ranks.get(name(a).toLowerCase()) ?? 4) -
      (ranks.get(name(b).toLowerCase()) ?? 4)
  )
}

export function mostRecentRepository(
  repositories: ReadonlyArray<{ full_name: string }>,
  usage: ReadonlyArray<RepositoryUsage> = []
): string | null {
  const available = new Map(
    repositories.map((repo) => [repo.full_name.toLowerCase(), repo.full_name])
  )
  const recent = [...usage]
    .filter((entry) => available.has(entry.repo.toLowerCase()))
    .sort((a, b) => Date.parse(b.last_used_at) - Date.parse(a.last_used_at))[0]
  return recent ? (available.get(recent.repo.toLowerCase()) ?? null) : null
}
