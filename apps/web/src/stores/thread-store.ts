import { create } from "zustand";

/**
 * Simplified thread store for UI state only
 * Data caching is handled by SWR hooks directly
 */
export interface ThreadStoreState {
  // UI state only - active thread tracking
  activeThreadId: string | null;

  // UI state only - global polling control
  isGlobalPollingEnabled: boolean;

  // Actions
  setActiveThread: (threadId: string | null) => void;
  setGlobalPolling: (enabled: boolean) => void;
}

/**
 * Minimal Zustand store for thread UI state management
 * All data caching moved to SWR for better performance and consistency
 */
export const useThreadStore = create<ThreadStoreState>((set) => ({
  // Initial state
  activeThreadId: null,
  isGlobalPollingEnabled: true,

  // Actions
  setActiveThread: (threadId) => {
    set({ activeThreadId: threadId });
  },

  setGlobalPolling: (enabled) => {
    set({ isGlobalPollingEnabled: enabled });
  },
}));

// Selector hooks for specific pieces of state
export const useActiveThreadId = () =>
  useThreadStore((state) => state.activeThreadId);

export const useGlobalPollingEnabled = () =>
  useThreadStore((state) => state.isGlobalPollingEnabled);
