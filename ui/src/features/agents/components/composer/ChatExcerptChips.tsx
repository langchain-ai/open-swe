import { XIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { FileCodeIcon } from "@phosphor-icons/react/dist/ssr/FileCode"
import { createContext } from "react"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"

export interface ChatExcerpts {
  excerpts?: ReadonlyArray<CodeExcerpt>
  removeExcerpt?: (index: number) => void
  clearExcerpts?: () => void
}

export const ChatExcerptsContext = createContext<ChatExcerpts | null>(null)

/** "R35-37" → "lines 35–37", with "old" for the deleted side. */
function excerptLines(label: string): string {
  const match = /^([LR])(\d+)(?:-(\d+))?$/.exec(label)
  if (!match) return label
  const [, side, start, end] = match
  const old = side === "L" ? "old " : ""
  return end ? `${old}lines ${start}–${end}` : `${old}line ${start}`
}

/** The code attached to the next message, as chips the reviewer can drop before sending. */
export function ChatExcerptChips({
  excerpts = [],
  removeExcerpt,
}: ChatExcerpts) {
  if (excerpts.length === 0) return null
  return (
    <ul
      aria-label="Selections attached to your next message"
      className="flex flex-wrap gap-space-2 px-space-1 pb-space-1"
    >
      {excerpts.map((excerpt, index) => (
        <li
          key={`${excerpt.path}:${excerpt.lineLabel}:${index}`}
          title={excerpt.snippet}
          className="flex max-w-full items-center gap-space-2 rounded-md border border-default bg-surface-level-2 py-0.5 pr-0.5 pl-space-2 text-xxs"
        >
          <FileCodeIcon
            className="size-3.5 shrink-0 text-secondary"
            weight="regular"
          />
          <span className="min-w-0 truncate font-mono">
            {excerpt.path.split("/").pop()}
          </span>
          <span className="shrink-0 text-secondary">
            {excerptLines(excerpt.lineLabel)}
          </span>
          <IconButton
            icon={XIcon}
            label={`Remove ${excerpt.path} ${excerptLines(excerpt.lineLabel)}`}
            size="xs"
            variant="plain"
            color="secondary"
            className="shrink-0"
            onClick={() => removeExcerpt?.(index)}
          />
        </li>
      ))}
    </ul>
  )
}
