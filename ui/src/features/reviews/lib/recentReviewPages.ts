export interface RecentReviewPage {
  owner: string
  repo: string
  number: number
  title: string
  author: string
  opened_at: string
}

const storageKey = (login: string) =>
  `open-swe.reviews.recent.v1.${login.toLowerCase()}`

export function readRecentReviewPages(login: string): Array<RecentReviewPage> {
  if (!login || typeof window === "undefined") return []
  try {
    const stored: unknown = JSON.parse(
      window.localStorage.getItem(storageKey(login)) ?? "[]"
    )
    if (!Array.isArray(stored)) return []
    return stored
      .filter(
        (entry): entry is RecentReviewPage =>
          entry !== null &&
          typeof entry === "object" &&
          typeof entry.owner === "string" &&
          typeof entry.repo === "string" &&
          Number.isSafeInteger(entry.number) &&
          entry.number > 0 &&
          typeof entry.title === "string" &&
          typeof entry.author === "string" &&
          typeof entry.opened_at === "string" &&
          Number.isFinite(Date.parse(entry.opened_at))
      )
      .slice(0, 50)
  } catch (error) {
    console.warn("Could not read recent review pages", error)
    return []
  }
}

export function recordRecentReviewPage(
  login: string,
  page: RecentReviewPage
): Array<RecentReviewPage> {
  const identity = (entry: RecentReviewPage) =>
    `${entry.owner}/${entry.repo}#${entry.number}`.toLowerCase()
  const recent = [
    page,
    ...readRecentReviewPages(login).filter(
      (entry) => identity(entry) !== identity(page)
    ),
  ].slice(0, 50)
  try {
    window.localStorage.setItem(storageKey(login), JSON.stringify(recent))
  } catch (error) {
    console.warn("Could not save recent review pages", error)
  }
  return recent
}
