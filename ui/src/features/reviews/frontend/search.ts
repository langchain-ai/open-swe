export interface FrontendReviewSearch {
  view?: "frontend"
  run?: string
}

export function validateFrontendReviewSearch(
  search: Record<string, unknown>
): FrontendReviewSearch {
  const run =
    typeof search.run === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      search.run
    )
      ? search.run
      : undefined
  return {
    view: search.view === "frontend" || run ? "frontend" : undefined,
    run,
  }
}
