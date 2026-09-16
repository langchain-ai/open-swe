import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type { TeamSettings } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"

export function ExpeditedReviewSection() {
  const qc = useQueryClient()
  const settings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
  })
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (body: TeamSettings) => api.saveTeamSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(["teamSettings"], saved)
      setError(null)
    },
    onError: (e: Error) => setError(e.message),
  })
  return (
    <SettingsSection
      title={
        <span className="inline-flex items-center gap-2">
          Expedited Slack review
          <Badge variant="outline">Experimental</Badge>
        </span>
      }
      description="Lets the agent ask for a pull request of at most 10 changed lines to be approved and merged from its Slack thread. The card appears only once every check GitHub requires is green and every review is clean; two people with write access approve, and their clicks become real GitHub reviews. Off by default."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="Allow expedited Slack review"
          description="When on, the agent gets the expedite_pr_approval tool in Slack threads. When off, open approval cards are withdrawn and no new ones are posted."
          control={
            <Switch
              checked={!!settings.data?.expedited_review_enabled}
              onCheckedChange={(next) =>
                settings.data &&
                save.mutate({
                  ...settings.data,
                  expedited_review_enabled: next,
                })
              }
              disabled={!settings.data || save.isPending}
            />
          }
        />
      </div>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
