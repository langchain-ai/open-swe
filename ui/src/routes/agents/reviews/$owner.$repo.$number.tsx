import { createFileRoute, redirect } from "@tanstack/react-router"

import { validateReviewsSearch } from "@/features/reviews/search"

export const Route = createFileRoute("/agents/reviews/$owner/$repo/$number")({
  validateSearch: validateReviewsSearch,
  beforeLoad: ({ params, search, location }) => {
    throw redirect({
      to: "/agents/reviews",
      search: {
        ...search,
        pr: `${params.owner}/${params.repo}#${params.number}`,
      },
      hash: location.hash,
      replace: true,
    })
  },
})
