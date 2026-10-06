import { SettingRow } from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
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
  description: string
  fallback?: boolean
}) {
  const { profile, ready, save } = useProfileSettings()
  return (
    <SettingRow
      density="compact"
      label={label}
      description={description}
      control={(slot) => (
        <Switch
          id={slot.id}
          aria-describedby={slot.describedById}
          checked={profile?.[field] ?? fallback}
          disabled={!ready}
          onCheckedChange={(value) => save({ [field]: value })}
        />
      )}
    />
  )
}
