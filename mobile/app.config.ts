import { ExpoConfig } from "expo/config"

const config: ExpoConfig = {
  name: "Robin",
  slug: "open-swe",
  scheme: "robin",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "automatic",
  ios: {
    appleTeamId: "H253X88X9F",
    bundleIdentifier: "com.langchain.robin",
    buildNumber: Date.now().toString(),
    supportsTablet: false,
    icon: "./assets/icon.icon",
    infoPlist: {
      NSAppTransportSecurity: { NSAllowsLocalNetworking: true },
      LSApplicationCategoryType: "public.app-category.developer-tools",
    },
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    "expo-web-browser",
    "expo-image",
  ],
  experiments: { typedRoutes: true },
}

export default config
