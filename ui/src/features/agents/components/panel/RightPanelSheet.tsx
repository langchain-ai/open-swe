import type { ReactNode } from "react"

import {
  Sheet,
  SheetContent,
} from "@langchain/gtm-platform-design-system/ui/sheet"
import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"

export function RightPanelSheet(props: {
  children: ReactNode
  open: boolean
  onClose: () => void
}) {
  return (
    <Sheet
      open={props.open}
      onOpenChange={(open) => {
        if (!open) props.onClose()
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        className={RIGHT_PANEL_SHEET_CLASS_NAME}
      >
        {props.children}
      </SheetContent>
    </Sheet>
  )
}
