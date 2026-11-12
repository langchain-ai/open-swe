import AsyncStorage from "@react-native-async-storage/async-storage"

import {
  gitState,
  workspaceKey,
  type AgentStatus,
  type AgentThread,
  type GitState,
} from "../core/models"

export type Grouping = "category" | "repo" | "none"

export const GROUPING_TITLES: Record<Grouping, string> = {
  category: "Category",
  repo: "Repo",
  none: "None",
}

/** How agent lists are grouped, filtered and drawn; set in the Customize sheet. */
export interface ListPreferences {
  grouping: Grouping
  /** Each filter is empty when it lets everything through. */
  repos: string[]
  statuses: AgentStatus[]
  gitStates: GitState[]
  sources: string[]
  showChanges: boolean
  showBranchStatus: boolean
  showRuntime: boolean
  showLastUpdated: boolean
}

export const DEFAULT_PREFERENCES: ListPreferences = {
  grouping: "category",
  repos: [],
  statuses: [],
  gitStates: [],
  sources: [],
  showChanges: true,
  showBranchStatus: true,
  showRuntime: true,
  showLastUpdated: false,
}

export function preferencesAllow(
  preferences: ListPreferences,
  thread: AgentThread
): boolean {
  const allows = <T>(selection: T[], value: T) =>
    selection.length === 0 || selection.includes(value)
  return (
    allows(preferences.repos, workspaceKey(thread)) &&
    allows(preferences.statuses, thread.status) &&
    allows(preferences.gitStates, gitState(thread)) &&
    allows(preferences.sources, thread.source ?? "dashboard")
  )
}

export function isDefaultPreferences(preferences: ListPreferences): boolean {
  return JSON.stringify(preferences) === JSON.stringify(DEFAULT_PREFERENCES)
}

const KEY = "listPreferences"

export async function loadPreferences(): Promise<ListPreferences> {
  try {
    const raw = await AsyncStorage.getItem(KEY)
    return raw
      ? {
          ...DEFAULT_PREFERENCES,
          ...(JSON.parse(raw) as Partial<ListPreferences>),
        }
      : DEFAULT_PREFERENCES
  } catch {
    return DEFAULT_PREFERENCES
  }
}

export function savePreferences(preferences: ListPreferences) {
  AsyncStorage.setItem(KEY, JSON.stringify(preferences)).catch(() => {})
}
