"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

export const DEFAULT_CONFIG_KEY = "open_swe_default_config";

interface ConfigState {
  configs: Record<string, any>;
  getConfig: (namespace: string) => Record<string, any>;
  getConfigs: () => Record<string, any>;
  updateConfig: (namespace: string, key: string, value: any) => void;
  resetConfig: (namespace: string) => void;
  resetStore: (namespace: string) => void;
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set, get) => ({
      configs: {},

      getConfig: (namespace: string) => {
        const state = get();
        const baseConfig = state.configs[namespace];
        const configObj = {
          ...baseConfig,
        };
        delete configObj.__defaultValues;
        return configObj;
      },

      getConfigs: () => {
        const state = get();
        return state.configs;
      },

      updateConfig: (namespace: string, key: string, value: any) =>
        set((state) => ({
          configs: {
            ...state.configs,
            [namespace]: {
              ...(state.configs[namespace] || {}),
              [key]: value,
            },
          },
        })),

      resetConfig: (namespace: string) => {
        set((state) => {
          const config = state.configs[namespace];
          if (!config || !config.__defaultValues) {
            return state;
          }
          const defaultsToUse = { ...config.__defaultValues };
          return {
            configs: {
              ...state.configs,
              [namespace]: defaultsToUse,
            },
          };
        });
      },

      resetStore: () => set({ configs: {} }),
    }),
    {
      name: "open-swe-config-storage",
    },
  ),
);
