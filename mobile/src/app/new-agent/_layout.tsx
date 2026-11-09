import { Stack } from "expo-router"
import { useState } from "react"

import { Theme, useHeaderTint } from "../../design/theme"
import {
  NewAgentDraft,
  NewAgentDraftContext,
} from "../../features/newAgentDraft"
import { useAgentStore } from "../../state/appModel"

export default function NewAgentLayout() {
  const store = useAgentStore()
  const [draft] = useState(() => new NewAgentDraft(store))
  const headerTint = useHeaderTint()
  return (
    <NewAgentDraftContext.Provider value={draft}>
      <Stack
        screenOptions={{
          headerTintColor: headerTint,
          headerTransparent: true,
          headerShadowVisible: false,
          headerTitleStyle: { color: headerTint },
          headerBackButtonDisplayMode: "minimal",
          contentStyle: { backgroundColor: Theme.surface },
        }}
      />
    </NewAgentDraftContext.Provider>
  )
}
