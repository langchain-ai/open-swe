import { Stack } from "expo-router"

import { Theme, useHeaderTint } from "../../design/theme"

export default function CustomizeLayout() {
  const headerTint = useHeaderTint()
  return (
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
  )
}
