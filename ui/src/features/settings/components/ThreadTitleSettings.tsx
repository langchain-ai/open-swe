import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { SettingsSection } from "@/components/AppShell"
import { useProfileSettings } from "./ProfileSwitchRow"

const MAX_INSTRUCTIONS_CHARS = 2000

/** How Robin titles the threads this person starts. */
export function ThreadTitleSettings() {
  const { profile, ready, save } = useProfileSettings()
  const [draft, setDraft] = useState<string | null>(null)
  const saved = profile?.thread_title_instructions ?? ""
  const value = draft ?? saved
  const dirty = value.trim() !== saved

  return (
    <SettingsSection
      title="Thread titles"
      description="How Robin names the threads you start. Leave empty for Robin's default short titles."
    >
      <div className="flex flex-col gap-space-2 p-space-4">
        <Textarea
          aria-label="Thread title instructions"
          size="md"
          rows={4}
          maxLength={MAX_INSTRUCTIONS_CHARS}
          value={value}
          onChange={setDraft}
          disabled={!ready}
          placeholder="e.g. Start every title with a lowercase tag: research:, review:, triage:, pr: or fix:. Include the repository and PR number when there is one."
        />
        <div className="flex items-center gap-space-2">
          <Button
            size="xs"
            color="primary"
            disabled={!ready || !dirty}
            onClick={() => save({ thread_title_instructions: value.trim() })}
          >
            Save
          </Button>
          {saved && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              disabled={!ready}
              onClick={() => {
                setDraft(null)
                save({ thread_title_instructions: "" })
              }}
            >
              Use default titles
            </Button>
          )}
          {dirty && (
            <span className="text-xs text-secondary">Unsaved changes</span>
          )}
        </div>
      </div>
    </SettingsSection>
  )
}
