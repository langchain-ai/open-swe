/** A remembered UI choice; storage that's blocked or full just means it isn't remembered. */
export function readPreference(key: string): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(key)
  } catch (error) {
    console.warn("Could not read a preference", { key, error })
    return null
  }
}

export function writePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch (error) {
    console.warn("Could not save a preference", { key, error })
  }
}
