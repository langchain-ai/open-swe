import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"

export function LeaderboardPrivacySection() {
  const qc = useQueryClient()
  const privacy = useQuery({
    queryKey: ["usageLeaderboardPrivacy"],
    queryFn: api.getUsageLeaderboardPrivacy,
  })
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (enabled: boolean) => api.saveUsageLeaderboardPrivacy(enabled),
    onSuccess: (_saved, enabled) => {
      qc.setQueryData(["usageLeaderboardPrivacy"], {
        usage_leaderboard_privacy_enabled: enabled,
      })
      // Identities change with the policy, so identified rows read under the
      // old policy must leave the cache entirely — invalidation alone would
      // keep serving them until the refetch lands.
      qc.removeQueries({ queryKey: ["usageLeaderboard"] })
      qc.removeQueries({ queryKey: ["prMergeRateByModel"] })
      setError(null)
    },
    onError: (e: Error) => setError(e.message),
  })
  return (
    <SettingsSection
      title="Leaderboard privacy"
      description="Controls what the usage leaderboard discloses to non-admins. On (the default): non-admins see every member except themself as an anonymous &quot;Open SWE user&quot; row — no GitHub login, email, avatar, or profile link, and no per-person fallback names that would act as stable pseudonyms. Admins and each viewer's own row stay identified. Off: every signed-in user sees names, GitHub handles, and avatars, but no member's email is ever shown to anyone but them. Applies to every workspace on this instance."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="Anonymize the leaderboard for non-admins"
          description="Sorting and pagination still work; anonymous rows sort by the shared label, so a member's hidden name cannot be inferred from their position."
          control={
            <Switch
              aria-label="Anonymize the leaderboard for non-admins"
              checked={!!privacy.data?.usage_leaderboard_privacy_enabled}
              onCheckedChange={(next) => save.mutate(next)}
              disabled={!privacy.data || save.isPending}
            />
          }
        />
      </div>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
