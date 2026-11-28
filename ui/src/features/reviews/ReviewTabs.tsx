import { Button } from "@langchain/macaw-components/Button"
import { ButtonGroup } from "@langchain/macaw-components/ButtonGroup"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { useNavigate, useSearch } from "@tanstack/react-router"

import { MenuCheckItem } from "@/features/agents/components/MenuCheckItem"
import { cn } from "@/lib/utils"
import { PILL_TAB_CLASS } from "./lib/styles"

const tabs = [
  ["mine", "Mine"],
  ["to-review", "To Review"],
  ["all", "All Reviews"],
] as const

type ReviewTabsProps =
  | { variant?: "tabs" }
  | { variant: "dropdown"; active: boolean; onShowList: () => void }

/** Switches between the Mine, To Review and All Reviews lists. */
export function ReviewTabs(props: ReviewTabsProps) {
  const tab =
    useSearch({ from: "/agents/reviews/", select: (search) => search.tab }) ??
    "mine"
  const navigate = useNavigate({ from: "/agents/reviews/" })

  if (props.variant === "dropdown") {
    const label = tabs.find(([value]) => value === tab)?.[1] ?? tabs[0][1]
    const selected = props.active ? "" : undefined
    return (
      <DropdownMenu>
        <ButtonGroup
          size="sm"
          color="secondary"
          variant="plain"
          className="shrink-0"
        >
          <Button
            className={cn(PILL_TAB_CLASS, "review-sidebar-list-pill pr-0")}
            aria-label={`Pull request list, ${label}`}
            aria-pressed={props.active}
            data-selected={selected}
            onClick={props.onShowList}
          >
            {label}
          </Button>
          <DropdownMenuTrigger asChild>
            <IconButton
              icon={CaretDownIcon}
              iconClassName="size-4"
              label="Choose pull request list"
              tooltipProps={{ disabled: true }}
              data-selected={selected}
              className={cn(PILL_TAB_CLASS, "review-sidebar-list-pill px-0")}
            />
          </DropdownMenuTrigger>
        </ButtonGroup>
        <DropdownMenuContent align="end">
          {tabs.map(([value, name]) => (
            <MenuCheckItem
              key={value}
              size="sm"
              type="radio"
              checked={tab === value}
              onSelect={() => {
                props.onShowList()
                if (value !== tab) {
                  void navigate({
                    search: (previous) => ({
                      ...previous,
                      tab: value,
                      page: undefined,
                    }),
                  })
                }
              }}
            >
              {name}
            </MenuCheckItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }

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
                pr: undefined,
                queue: undefined,
                files: undefined,
                at: undefined,
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
