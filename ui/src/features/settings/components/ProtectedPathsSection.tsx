import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import {
  useInstanceSettings,
  type InstanceSettings,
} from "@/features/settings/lib/settingsScope"

/** The paths whose changes Robin pushes only after the thread's owner approves the diff. */
export function ProtectedPathsSection() {
  const settings = useInstanceSettings()
  return (
    <SettingsSection
      title="Protected Paths"
      description="When Robin pushes a change to one of these paths, in any repository, the push waits until the thread's owner approves that exact diff."
    >
      <ProtectedPathsEditor
        // A newly saved list starts a fresh draft.
        key={(settings.saved?.protected_paths ?? []).join("\n")}
        settings={settings}
      />
    </SettingsSection>
  )
}

function ProtectedPathsEditor({ settings }: { settings: InstanceSettings }) {
  const [draft, setDraft] = useState(
    (settings.saved?.protected_paths ?? []).join("\n")
  )
  const paths = draft
    .split("\n")
    .map((path) => path.trim())
    .filter(Boolean)
  const dirty =
    paths.join("\n") !== (settings.data?.protected_paths ?? []).join("\n")
  return (
    <div>
      <SettingsRow
        label="Paths"
        description="With no paths, pushes never wait for approval."
        control={null}
      />
      <div className="flex flex-col gap-space-2 px-space-4 pb-space-4">
        <Textarea
          aria-label="Protected paths"
          size="md"
          hintText="One path per line, relative to the repository root. A directory covers everything under it; * stays within one directory and **/ spans any depth."
          value={draft}
          onChange={setDraft}
          disabled={!settings.data}
        />
        <div className="flex items-center gap-space-2">
          <Button
            size="xs"
            color="primary"
            disabled={!settings.data || !dirty}
            onClick={() => settings.save({ protected_paths: paths })}
          >
            Save paths
          </Button>
          {dirty && (
            <span className="text-xs text-secondary">Unsaved changes</span>
          )}
        </div>
      </div>
    </div>
  )
}
