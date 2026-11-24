import { Children } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Text } from "@langchain/macaw-components/Text"
import { CircleIcon } from "@phosphor-icons/react/dist/ssr/Circle"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { LinkIcon } from "@phosphor-icons/react/dist/ssr/Link"
import type { ReactNode } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import { cn } from "@/lib/utils"

/** The responsive table grid for Connections. */
export function ConnectionsTable({
  detail,
  empty,
  children,
}: {
  /** The heading of the column between the name and the status. */
  detail: string
  /** Shown in place of the rows when there are none. */
  empty: ReactNode
  children: ReactNode
}) {
  const rows = Children.toArray(children)
  const hasRows = rows.length > 0
  return (
    <div className="@container">
      <table className="w-full table-fixed text-left">
        <thead className="bg-surface-level-2 text-xs text-tertiary">
          <tr className={cn(hasRows && "border-b border-subtle")}>
            <th
              scope="col"
              className="py-space-2 pr-space-3 pl-space-4 font-normal @xl:pl-space-5"
            >
              Name
            </th>
            <th
              scope="col"
              className="hidden w-56 py-space-2 pr-space-3 font-normal @xl:table-cell @3xl:w-72"
            >
              {detail}
            </th>
            <th
              scope="col"
              className="w-32 py-space-2 text-center font-normal @xl:w-36"
            >
              Status
            </th>
            <th
              scope="col"
              className="w-11 py-space-2 pr-space-4 @xl:w-14 @xl:pr-space-5"
            >
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        {hasRows ? <tbody>{rows}</tbody> : null}
      </table>
      {!hasRows ? (
        <div className="flex h-32 items-center justify-center px-space-4 text-center">
          <Text as="span" role="status" variant="sm" color="tertiary">
            {empty}
          </Text>
        </div>
      ) : null}
    </div>
  )
}

export interface ConnectionAction {
  label: string
  onSelect: () => void
  disabled?: boolean
}

/** One connection row: logo and name, details, status badge/action, and overflow menu. */
export function ConnectionRow({
  name,
  logo,
  detail,
  detailTitle,
  showDetailInName = false,
  status,
  actions,
}: {
  name: string
  logo: ReactNode
  detail: ReactNode
  /** The full detail, for when the column truncates it. */
  detailTitle?: string
  /** Keeps the detail visible under the name when the detail column hides on narrow widths. */
  showDetailInName?: boolean
  status: ReactNode
  actions: ConnectionAction[]
}) {
  return (
    <tr className="border-b border-subtle last:border-b-0 hover:bg-surface-level-2">
      <td className="max-w-0 py-space-3 pr-space-3 pl-space-4 @xl:pl-space-5">
        <div className="flex min-w-0 items-center gap-space-3">
          {logo}
          <div className="min-w-0">
            <Text
              as="span"
              variant="sm"
              weight="medium"
              className="block truncate"
            >
              {name}
            </Text>
            {showDetailInName ? (
              <Text
                as="span"
                variant="xs"
                color="secondary"
                className="block truncate @xl:hidden"
                title={detailTitle}
              >
                {detail}
              </Text>
            ) : null}
          </div>
        </div>
      </td>
      <td className="hidden max-w-0 py-space-3 pr-space-3 @xl:table-cell">
        <Text
          as="span"
          variant="sm"
          color="secondary"
          className="block truncate"
          title={detailTitle}
        >
          {detail}
        </Text>
      </td>
      <td className="py-space-3 text-center">{status}</td>
      <td className="py-space-3 pr-space-4 text-right @xl:pr-space-5">
        {actions.length ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconButton
                icon={DotsThreeIcon}
                label={`Actions for ${name}`}
                size="sm"
                variant="plain"
                color="secondary"
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {actions.map((action) => (
                <DropdownMenuItem
                  key={action.label}
                  disabled={action.disabled}
                  onSelect={action.onSelect}
                >
                  {action.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </td>
    </tr>
  )
}

export function ConnectionStatusBadge({ connected }: { connected: boolean }) {
  return (
    <Badge
      size="md"
      color={connected ? "success" : "secondary"}
      leftDecorator={connected ? CircleIcon : undefined}
      iconWeight="fill"
      className="gap-space-2 whitespace-nowrap [&>svg]:size-1.5"
    >
      {connected ? "Connected" : "Not connected"}
    </Badge>
  )
}

/** The status column's call to connect; the button around it owns the action. */
export function ConnectBadge({ children }: { children: string }) {
  return (
    <Badge
      size="md"
      color="primary"
      leftDecorator={LinkIcon}
      className="whitespace-nowrap"
    >
      {children}
    </Badge>
  )
}

/** Strips a Button down so the ConnectBadge inside it is all that shows. */
export const CONNECT_BADGE_BUTTON = "h-auto rounded-full border-0 p-0"
