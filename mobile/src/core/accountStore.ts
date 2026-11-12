import AsyncStorage from "@react-native-async-storage/async-storage"
import * as SecureStore from "expo-secure-store"

/** The signed-in backend and its session, kept in the Keychain on this device only. */
export interface StoredAccount {
  backend: string
  sessionToken: string
}

const ACCOUNT_KEY = "dev.langchain.openswe.account"
const LAST_BACKEND_KEY = "lastBackend"
const KEYCHAIN = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
}

export const AccountStore = {
  async load(): Promise<StoredAccount | null> {
    const raw = await SecureStore.getItemAsync(ACCOUNT_KEY, KEYCHAIN)
    if (!raw) return null
    try {
      const parsed = JSON.parse(raw) as Partial<StoredAccount>
      return typeof parsed.backend === "string" &&
        typeof parsed.sessionToken === "string"
        ? { backend: parsed.backend, sessionToken: parsed.sessionToken }
        : null
    } catch {
      return null
    }
  },

  async save(account: StoredAccount): Promise<void> {
    await SecureStore.setItemAsync(
      ACCOUNT_KEY,
      JSON.stringify(account),
      KEYCHAIN
    )
  },

  async clear(): Promise<void> {
    await SecureStore.deleteItemAsync(ACCOUNT_KEY, KEYCHAIN)
  },

  /** The backend last signed in to, kept outside the Keychain so the sign-in form can prefill it. */
  async lastBackend(): Promise<string> {
    return (await AsyncStorage.getItem(LAST_BACKEND_KEY)) ?? ""
  },

  async setLastBackend(backend: string): Promise<void> {
    await AsyncStorage.setItem(LAST_BACKEND_KEY, backend)
  },
}
