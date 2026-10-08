import type { ReactNode } from "react"

import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import type { ProfileUpdate } from "@/lib/api"
import { usePatchProfile, useProfile } from "@/lib/profile"

type ProfileFlag = {
  [K in keyof ProfileUpdate]-?: NonNullable<ProfileUpdate[K]> extends boolean
    ? K
    : never
}[keyof ProfileUpdate]

/** The profile plus a partial settings save. */
export function useProfileSettings() {
  const profile = useProfile()
  const patch = usePatchProfile()
  return {
    profile: profile.data,
    ready: profile.isSuccess,
    save: (update: Partial<ProfileUpdate>) => patch.patch(update),
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
          checked={profile?.[field] ?? fallback}
          disabled={!ready}
          onCheckedChange={(value) => save({ [field]: value })}
        />
      }
    />
  )
}
