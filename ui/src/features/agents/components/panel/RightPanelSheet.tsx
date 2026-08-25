import type { ReactNode } from "react"

import { Sheet, SheetPopup } from "@/components/ui/sheet"
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
      <SheetPopup
        side="right"
        showCloseButton={false}
        keepMounted
        className={RIGHT_PANEL_SHEET_CLASS_NAME}
      >
        {props.children}
      </SheetPopup>
    </Sheet>
  )
}
