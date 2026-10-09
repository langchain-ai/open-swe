import type { ReactNode } from "react"

import { SideSheet } from "@/components/SideSheet"
import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"

/** The work panel as a right-anchored modal sheet, for windows too narrow to fit it inline. */
export function RightPanelSheet(props: {
  children: ReactNode
  open: boolean
  onClose: () => void
}) {
  return (
    <SideSheet
      side="right"
      title="Work panel"
      description="Changes, terminals, and files for this thread."
      open={props.open}
      onClose={props.onClose}
      className={RIGHT_PANEL_SHEET_CLASS_NAME}
    >
      {props.children}
    </SideSheet>
  )
}
