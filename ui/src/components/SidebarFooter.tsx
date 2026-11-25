import type { ReactNode } from "react"

import { SidebarUsageMeter } from "@/components/SidebarUsageMeter"
import type { SessionUser } from "@/lib/api"

/**
 * The sidebar's pinned bottom: the usage meter above the account control.
 * Every sidebar renders it, so it must lay out identically in each.
 */
export function SidebarFooter({
  user,
  account,
  action,
  onNavigate,
}: {
  user: SessionUser | null
  account: ReactNode
  action?: ReactNode
  onNavigate?: () => void
}) {
  return (
    <div className="flex flex-col gap-space-1 p-space-2">
      {user && (
        <SidebarUsageMeter
          userId={user.user_id ?? user.login}
          onNavigate={onNavigate}
        />
      )}
      <div className="flex items-center gap-space-2">
        <div className="flex min-w-0 flex-1">{account}</div>
        {action}
      </div>
    </div>
  )
}
