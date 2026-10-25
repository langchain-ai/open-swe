import { IconButton } from "@langchain/macaw-components/IconButton"
import { TextAlignLeftIcon } from "@phosphor-icons/react/dist/ssr/TextAlignLeft"

import { useDiffWrap } from "@/features/agents/utils/diffUtils"
import { cn } from "@/lib/utils"

export function DiffWrapToggle({ className }: { className?: string }) {
  const [wrap, setWrap] = useDiffWrap()

  return (
    <IconButton
      icon={TextAlignLeftIcon}
      label="Wrap lines"
      size="xs"
      color="secondary"
      variant="plain"
      onClick={() => setWrap(!wrap)}
      aria-pressed={wrap}
      className={cn(
        "max-md:min-h-11 max-md:min-w-11",
        wrap && "bg-selected text-primary",
        className
      )}
    />
  )
}
