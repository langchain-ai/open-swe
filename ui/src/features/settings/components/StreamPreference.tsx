import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsRow } from "@/components/AppShell"

import {
  setUseStreamPreference,
  useStreamPreference,
} from "@/lib/streamPreference"

export function StreamPreference() {
  const preferStream = useStreamPreference()

  return (
    <SettingsRow
      label="SDK useStream"
      htmlFor="sdk-use-stream"
      description="Use SDK streaming instead of the transcript with the same conversation UI. Applies only to this browser; legacy threads always use SDK streaming."
      control={
        <Switch
          id="sdk-use-stream"
          aria-label="SDK useStream"
          checked={preferStream}
          onChange={setUseStreamPreference}
        />
      }
    />
  )
}
