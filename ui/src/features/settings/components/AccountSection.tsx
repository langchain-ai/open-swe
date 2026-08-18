import { useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"

import type { SessionUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { clearCachedRepos } from "@/lib/repoCache"

export function AccountSection({ user }: { user: SessionUser }) {
  const qc = useQueryClient()
  const navigate = useNavigate()

  const logout = async () => {
    await api.logout()
    clearCachedRepos()
    qc.setQueryData(["session"], null)
    void navigate({ to: "/login" })
  }

  return (
    <SettingsSection title="Account">
      <SettingsRow
        label="GitHub account"
        control={
          <span className="text-xs text-muted-foreground">{user.login}</span>
        }
      />
      <SettingsRow
        label="Email"
        control={
          <span className="text-xs text-muted-foreground">
            {user.email ?? "—"}
          </span>
        }
      />
      <SettingsRow
        label="Sign out"
        description="End your dashboard session on this device."
        control={
          <Button size="sm" variant="outline" onClick={() => void logout()}>
            Sign out
          </Button>
        }
      />
    </SettingsSection>
  )
}
