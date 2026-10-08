/** The review detail's cache key; kept apart so routes can watch it without loading the page's modules. */
export function reviewDetailKey(owner: string, repo: string, number: number) {
  return ["review", owner, repo, number] as const
}
