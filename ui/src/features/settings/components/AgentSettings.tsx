import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { SettingsSection } from "@/components/AppShell"
import { usePatchProfile, useProfile } from "@/lib/profile"
import { ProfileSwitchRow } from "./ProfileSwitchRow"

export function AgentSettings() {
  const profile = useProfile()
  const save = usePatchProfile()
  const [draft, setDraft] = useState<string | null>(null)
  const saved = profile.data?.review_assignment_instructions ?? ""
  const value = draft ?? saved

  return (
    <SettingsSection title="Runs">
      <ProfileSwitchRow
        field="auto_human_review_enabled"
        label="Allow automatic review picks"
        description="Let Open SWE pick you for human reviews. People can still name you explicitly when this is off."
        fallback
      />
      <div className="flex flex-col gap-space-3 p-space-4">
        <Textarea
          label="Review Assignment Instructions"
          size="md"
          rows={4}
          maxLength={4000}
          value={value}
          onChange={setDraft}
          disabled={!profile.data}
          placeholder="Prefer backend and database reviews in langchainplus, not smith-frontend changes."
          hintText="Ask Open SWE to update these, or edit them here. Used with changed paths and ownership to guide automatic picks, including agent fallbacks. Preferences are not guaranteed exclusions and cannot override eligibility checks; turn off automatic picks above to opt out entirely."
        />
        <div>
          <Button
            size="xs"
            color="primary"
            disabled={!profile.data || save.isPending || value === saved}
            onClick={() => {
              save.patch({ review_assignment_instructions: value })
              setDraft(null)
            }}
          >
            Save instructions
          </Button>
        </div>
      </div>
    </SettingsSection>
  )
}
