"use client";

import { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Settings } from "lucide-react";
import { ConfigField } from "@/components/configuration/config-field";
import { useConfigStore, DEFAULT_CONFIG_KEY } from "@/hooks/useConfigStore";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import type { ConfigurableFieldUIMetadata } from "@open-swe/shared/configurable-metadata";
import { GraphConfigurationMetadata } from "@open-swe/shared/open-swe/types";
import { cn } from "@/lib/utils";

/**
 * Extract configuration metadata from the GraphConfiguration Zod schema
 */
function extractConfigurationsFromSchema(
  configurable: Record<string, any>,
): ConfigurableFieldUIMetadata[] {
  const configurations: ConfigurableFieldUIMetadata[] = [];

  for (const [label, { x_open_swe_ui_config: metadata }] of Object.entries(
    GraphConfigurationMetadata,
  )) {
    if (metadata.type === "hidden") {
      continue;
    }
    configurations.push({
      label,
      type: metadata.type,
      default: configurable[label] || metadata.default,
      description: metadata.description,
      placeholder: metadata.placeholder,
      options: metadata.options,
      min: metadata.min,
      max: metadata.max,
      step: metadata.step,
    });
  }

  return configurations;
}

export function ConfigManager() {
  const { configs, updateConfig, getConfig } = useConfigStore();
  const [defaultConfig, setDefaultConfig] = useState<
    ConfigurableFieldUIMetadata[]
  >([]);
  const [configurations, setConfigurations] = useState<
    ConfigurableFieldUIMetadata[]
  >([]);
  const [loading, setLoading] = useState(false);

  const loadConfigurations = async () => {
    // TODO: If we implement a concept of users and start storing config on assistants,
    // we will need to update this to fetch configs from the assistant first.
    setLoading(true);
    setDefaultConfig(extractConfigurationsFromSchema({}));

    if (getConfig(DEFAULT_CONFIG_KEY)) {
      const actualConfigs = extractConfigurationsFromSchema(
        getConfig(DEFAULT_CONFIG_KEY),
      );
      setConfigurations(actualConfigs);
      setLoading(false);
      return;
    }
    const actualConfigs = extractConfigurationsFromSchema({});
    actualConfigs.forEach((c) => {
      updateConfig(DEFAULT_CONFIG_KEY, c.label, c.default);
    });
    setConfigurations(actualConfigs);
    setLoading(false);
  };

  useEffect(() => {
    loadConfigurations();
  }, []);

  const hasConfiguredValues = configurations.some((config) => {
    const currentValue = configs[DEFAULT_CONFIG_KEY]?.[config.label];
    const defaultValue = defaultConfig.find(
      (c) => c.label === config.label,
    )?.default;
    return currentValue !== undefined && currentValue !== defaultValue;
  });

  return (
    <div className="space-y-8">
      <Card className="bg-card border-border shadow-sm">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2 text-xl">
                <Settings className="h-5 w-5" />
                Agent Configuration
              </CardTitle>
              <CardDescription>
                Configure agent behavior and model parameters. Will auto-save
                changes.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {hasConfiguredValues && (
                <Badge
                  variant="outline"
                  className={cn(
                    "text-xs",
                    "border-blue-200 bg-blue-50 text-blue-700",
                    "dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-400",
                  )}
                >
                  Customized
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : configurations.length > 0 ? (
            configurations.map(
              (config: ConfigurableFieldUIMetadata, index: number) => (
                <div
                  key={`${config.label}-${index}`}
                  className="border-border rounded-md border-[1px] p-4"
                >
                  <div className="mb-3 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {(() => {
                        const currentValue =
                          configs[DEFAULT_CONFIG_KEY]?.[config.label];
                        const defaultValue = defaultConfig.find(
                          (c) => c.label === config.label,
                        )?.default;
                        const isModified =
                          currentValue !== undefined &&
                          currentValue !== defaultValue;

                        return (
                          isModified && (
                            <Badge
                              variant="outline"
                              className={cn(
                                "text-xs",
                                "border-orange-200 bg-orange-50 text-orange-700",
                                "dark:border-orange-800 dark:bg-orange-900/20 dark:text-orange-400",
                              )}
                            >
                              Modified
                            </Badge>
                          )
                        );
                      })()}
                    </div>
                  </div>

                  <div className="space-y-3">
                    <ConfigField
                      id={config.label}
                      label={config.label}
                      type={
                        config.type === "boolean"
                          ? "switch"
                          : (config.type ?? "text")
                      }
                      description={config.description}
                      placeholder={config.placeholder}
                      options={config.options}
                      min={config.min}
                      max={config.max}
                      step={config.step}
                      value={configs[DEFAULT_CONFIG_KEY]?.[config.label]}
                      setValue={(value) => {
                        console.log("setting value!", value);
                        updateConfig(DEFAULT_CONFIG_KEY, config.label, value);
                      }}
                    />

                    <div className="flex items-center justify-between">
                      {(() => {
                        const currentValue =
                          configs[DEFAULT_CONFIG_KEY]?.[config.label];
                        const defaultValue = defaultConfig.find(
                          (c) => c.label === config.label,
                        )?.default;
                        const isModified =
                          currentValue !== undefined &&
                          currentValue !== defaultValue;

                        return (
                          isModified && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                updateConfig(
                                  DEFAULT_CONFIG_KEY,
                                  config.label,
                                  defaultValue,
                                )
                              }
                            >
                              Reset to Default
                            </Button>
                          )
                        );
                      })()}
                    </div>
                  </div>
                </div>
              ),
            )
          ) : (
            <div className="py-8 text-center">
              <Settings className="text-muted-foreground mx-auto mb-4 h-12 w-12" />
              <h3 className="mb-2 text-lg font-semibold">
                No Configuration Available
              </h3>
              <p className="text-muted-foreground">
                No configurable parameters found for the current context.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
