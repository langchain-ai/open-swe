import { Navigate } from "@tanstack/react-router"

import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export function LegacySettingsRedirect() {
  const session = useSession()
  if (session.isLoading) return null
  if (!session.data) return <RequireLogin />
  return <Navigate to={session.data.is_admin ? "/admin" : "/agents"} replace />
}
