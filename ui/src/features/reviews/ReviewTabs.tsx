import { Button } from "@langchain/macaw-components/Button"
import { useNavigate, useSearch } from "@tanstack/react-router"

const tabs = [
  ["mine", "Mine"],
  ["to-review", "To Review"],
  ["all", "All Reviews"],
] as const

/** Switches between the Mine, To Review and All Reviews lists. */
export function ReviewTabs({
  keepSelection = false,
}: {
  keepSelection?: boolean
}) {
  const tab =
    useSearch({ from: "/agents/reviews/", select: (search) => search.tab }) ??
    "mine"
  const navigate = useNavigate({ from: "/agents/reviews/" })
  return (
    <div className="flex items-center gap-space-1">
      {tabs.map(([value, label]) => (
        <Button
          key={value}
          color="secondary"
          variant={tab === value ? "normal" : "plain"}
          aria-pressed={tab === value}
          onClick={() => {
            void navigate({
              search: (previous) => ({
                ...previous,
                tab: value,
                page: undefined,
                ...(keepSelection
                  ? {}
                  : {
                      pr: undefined,
                      queue: undefined,
                      files: undefined,
                      at: undefined,
                    }),
              }),
            })
          }}
        >
          {label}
        </Button>
      ))}
    </div>
  )
}
