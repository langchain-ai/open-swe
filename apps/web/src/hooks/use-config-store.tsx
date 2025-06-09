"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ConfigurableFieldUIMetadata } from "@open-swe/shared/configurable-metadata";

export const DEFAULT_CONFIG_KEY = "open_swe_default_config";

interface ConfigState {
  configs: Record<string, any>;
  getConfig: (key: string) => Record<string, any>;
  getConfigs: () => Record<string, any>;
  updateConfig: (namespace: string, key: string, value: any) => void;
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

      resetConfig: (key: string) => {
        set((state) => {
          const config = state.configs[key];
          if (!config || !config.__defaultValues) {
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

      resetStore: () => set({ configs: {} }),
    }),
    {
      name: "open-swe-config-storage",
    },
  ),
);
