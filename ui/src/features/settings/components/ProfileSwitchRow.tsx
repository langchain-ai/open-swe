import type { ReactNode } from "react"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsRow } from "@/components/AppShell"
import type { ProfileUpdate } from "@/lib/api"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

type ProfileFlag = {
  [K in keyof ProfileUpdate]-?: NonNullable<ProfileUpdate[K]> extends boolean
    ? K
    : never
}[keyof ProfileUpdate]

/** The profile plus a save that fills unloaded model fields with the workspace defaults. */
export function useProfileSettings() {
  const profile = useProfile()
  const options = useOptions()
  const patch = usePatchProfile()
  const defaults = options.data
  return {
    profile: profile.data,
    options: defaults,
    ready: profile.isSuccess && !!defaults,
    save: (update: Partial<ProfileUpdate>) => {
      if (!defaults) return
      patch.patch(
        update,
        defaults.default_agent_model,
        defaults.default_agent_reasoning_effort
      )
    },
  }
}

/** A settings row whose switch reads and writes one boolean profile field. */
export function ProfileSwitchRow({
  field,
  label,
  description,
  fallback = false,
}: {
  field: ProfileFlag
  label: string
  description: ReactNode
  fallback?: boolean
}) {
  const { profile, ready, save } = useProfileSettings()
  return (
    <SettingsRow
      label={label}
      htmlFor={field}
      description={description}
      control={
        <Switch
          id={field}
          aria-label={label}
          checked={profile?.[field] ?? fallback}
          disabled={!ready}
          onChange={(value) => save({ [field]: value })}
        />
      }
    />
  )
}
