import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router"
import { StatusBar } from "expo-status-bar"
import { useEffect, useState } from "react"
import { Alert, AppState, useColorScheme } from "react-native"
import { GestureHandlerRootView } from "react-native-gesture-handler"

import { Theme, useHeaderTint } from "../design/theme"
import { AppModel, AppModelContext, useAppModel } from "../state/appModel"
import { useObserve } from "../state/observable"

export default function RootLayout() {
  const [model] = useState(() => new AppModel())
  const scheme = useColorScheme()
  useEffect(() => {
    model.restore()
  }, [model])
  return (
    <GestureHandlerRootView
      style={{ flex: 1, backgroundColor: Theme.background }}
    >
      <AppModelContext.Provider value={model}>
        {/* Native headers take their light or dark style from the navigation theme. */}
        <ThemeProvider value={scheme === "dark" ? DarkTheme : DefaultTheme}>
          <StatusBar style="auto" />
          <Navigator />
        </ThemeProvider>
      </AppModelContext.Provider>
    </GestureHandlerRootView>
  )
}

function Navigator() {
  const model = useAppModel()
  const store = useObserve(model.store)
  useInboxRefresh()
  useErrorAlert()
  const headerTint = useHeaderTint()
  if (model.isRestoring) return null
  const signedIn = store !== null
  return (
    <Stack
      screenOptions={{
        headerTintColor: headerTint,
        headerTransparent: true,
        headerShadowVisible: false,
        headerLargeTitleShadowVisible: false,
        headerLargeTitleStyle: { fontWeight: "500", color: headerTint },
        headerTitleStyle: { color: headerTint },
        headerBackButtonDisplayMode: "minimal",
        contentStyle: { backgroundColor: Theme.background },
      }}
    >
      <Stack.Protected guard={!signedIn}>
        <Stack.Screen name="sign-in" options={{ headerShown: false }} />
      </Stack.Protected>
      <Stack.Protected guard={signedIn}>
        <Stack.Screen
          name="index"
          options={{ title: "Inbox", headerLargeTitle: true }}
        />
        <Stack.Screen name="list" options={{ headerLargeTitle: true }} />
        <Stack.Screen name="thread/[id]" />
        <Stack.Screen name="changes/[id]" options={{ title: "" }} />
        <Stack.Screen
          name="details/[id]"
          options={{
            presentation: "formSheet",
            sheetAllowedDetents: [0.5, 1],
            sheetGrabberVisible: false,
            headerShown: false,
            contentStyle: { backgroundColor: Theme.surface },
          }}
        />
        <Stack.Screen
          name="customize"
          options={{ presentation: "modal", headerShown: false }}
        />
        <Stack.Screen
          name="new-agent"
          options={{ presentation: "modal", headerShown: false }}
        />
      </Stack.Protected>
    </Stack>
  )
}

/** Keep the inbox current while the app is open, as the dashboard does. */
function useInboxRefresh() {
  const store = useAppModel().store
  useEffect(() => {
    if (!store) return
    let timer: ReturnType<typeof setInterval> | null = null
    const start = () => {
      store.refresh()
      timer ??= setInterval(() => store.refresh(), 20_000)
    }
    const stop = () => {
      if (timer) clearInterval(timer)
      timer = null
    }
    start()
    const subscription = AppState.addEventListener("change", (state) =>
      state === "active" ? start() : stop()
    )
    return () => {
      stop()
      subscription.remove()
    }
  }, [store])
}

function useErrorAlert() {
  const store = useObserve(useAppModel().store)
  const message = store?.errorMessage
  useEffect(() => {
    if (!store || !message) return
    Alert.alert("Something went wrong", message, [
      { text: "OK", onPress: () => store.dismissError() },
    ])
  }, [store, message])
}
