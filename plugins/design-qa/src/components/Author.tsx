import { Avatar } from "@langchain/macaw-components/Avatar"
import { cn } from "@langchain/macaw-components/utils/cn"
import type { Person } from "@openswe/plugin-sdk"

import { useImage } from "../lib/media"

/** Who opened a pull request: their picture, name and GitHub handle. */
export function Author({
  login,
  person,
  className,
}: {
  login: string
  person: Person | undefined
  className?: string
}) {
  const { image } = useImage(person?.avatar ?? undefined)
  const name = person?.name || login
  return (
    <span
      className={cn("flex min-w-0 items-center gap-space-1", className)}
      title={`Opened by ${name === login ? login : `${name} (@${login})`}`}
    >
      <Avatar size="xs" shape="circle" label={name} imageUrl={image?.url} />
      <span className="truncate">{name}</span>
      {name.toLowerCase() !== login.toLowerCase() && (
        <span className="truncate text-tertiary">@{login}</span>
      )}
    </span>
  )
}
