import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { WrapText } from "@/components/glyphs"
import { PanelIconButton } from "@/features/agents/components/panel/PanelIconButton"
import { useDiffWrap } from "@/features/agents/utils/diffUtils"

export function DiffWrapToggle({ className }: { className?: string }) {
  const [wrap, setWrap] = useDiffWrap()

  return (
    <PanelIconButton
      label="Wrap lines"
      pressed={wrap}
      onClick={() => setWrap(!wrap)}
      className={className}
    >
      <Icon icon={WrapText} size="sm" />
    </PanelIconButton>
  )
}
