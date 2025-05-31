"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { ConfigurableFieldUIMetadata } from "@/types/configurable";

// TODO: Update ConfigState to:
// Replace these types with shared types from open-swe
// Use shared config store from open-swe, instead of previous Open Agent Config Interface

interface ConfigState {
  configs: Record<string, any>;
  getConfig: (key: string) => Record<string, any>;
  updateConfig: (key: string, value: any) => void;
  resetConfig: (key: string) => void;
  setDefaultConfig: (
    key: string,
    configurations: ConfigurableFieldUIMetadata[],
  ) => void;
  resetStore: (key: string) => void;
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set, get) => ({
      configs: {},

      getConfig: (key: string) => {
        const state = get();
        const baseConfig = state.configs[key];
        const configObj = {
          ...baseConfig,
        };
        delete configObj.__defaultValues;
        return configObj;
      },

      updateConfig: (key, value) =>
        set((state) => ({
          configs: {
            ...state.configs,
            [key]: {
              ...(state.configs[key] || {}),
              [key]: value,
            },
          },
        })),

      resetConfig: (key: string) => {
        set((state) => {
          const config = state.configs[key];
          if (!config || !config.__defaultValues) {
            // If no config or default values exist for this agent, do nothing or set to empty
            return state;
          }
          const defaultsToUse = { ...config.__defaultValues };
          return {
            configs: {
              ...state.configs,
              [key]: defaultsToUse,
            },
          };
        });
      },

      setDefaultConfig: (
        key: string,
        configurations: ConfigurableFieldUIMetadata[],
      ) => {
        const defaultConfig: Record<string, any> = {};
        configurations.forEach((config: ConfigurableFieldUIMetadata) => {
          if (config.default !== undefined) {
            defaultConfig[config.label] = config.default;
          }
        });

        defaultConfig.__defaultValues = { ...defaultConfig };

        set((currentState) => ({
          configs: {
            ...currentState.configs,
            [key]: defaultConfig,
          },
        }));
      },

      // Clear everything from the store
      resetStore: () => set({ configs: {} }),
    }),
    {
      name: "ai-config-storage", // Keep the same storage key, but manage agents inside
    },
  ),
);
