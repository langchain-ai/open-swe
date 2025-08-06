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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Settings, AlertTriangle, CircleAlert } from "lucide-react";
import { ConfigField } from "@/components/configuration/config-field";
import { useConfigStore, DEFAULT_CONFIG_KEY } from "@/hooks/useConfigStore";
import { Skeleton } from "@/components/ui/skeleton";
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

    // Extract default configurations from schema
    const defaultConfigs = extractConfigurationsFromSchema({});
    setDefaultConfig(defaultConfigs);

    // Get existing user configurations (if any)
    const existingConfig = getConfig(DEFAULT_CONFIG_KEY) || {};

    // Create configurations array with user values where they exist, defaults otherwise
    const actualConfigs = defaultConfigs.map((config) => ({
      ...config,
      // Use existing user value if it exists, otherwise keep the default for display
      default:
        existingConfig[config.label] !== undefined
          ? existingConfig[config.label]
          : config.default,
    }));

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
    // Only consider it configured if the user has explicitly set a value
    return currentValue !== undefined && currentValue !== defaultValue;
  });

  return (
    <div className="space-y-8">
      <Alert className="border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-400">
        <AlertTriangle className="h-4 w-4" />
        <AlertDescription>
          <strong>Warning:</strong> The default configuration values have been
          carefully selected for optimal performance. Modifying these settings
          may negatively impact the agent's performance and behavior. Only
          change these values if you understand their implications.
        </AlertDescription>
      </Alert>
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
                    {config.label === "mcpServers" && (
                      <Alert className="border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-400">
                        <CircleAlert className="h-4 w-4" />
                        <AlertDescription>
                          <p>
                            <strong>Notice:</strong> Open SWE{" "}
                            <i className="underline underline-offset-2">only</i>{" "}
                            supports MCP servers with <strong>HTTP</strong> or{" "}
                            <strong>SSE</strong> transports (with the exception
                            of the default LangGraph documentation MCP server).
                            Other transports will be <strong>ignored</strong>.
                          </p>
                        </AlertDescription>
                      </Alert>
                    )}
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
                      value={
                        configs[DEFAULT_CONFIG_KEY]?.[config.label] !==
                        undefined
                          ? configs[DEFAULT_CONFIG_KEY][config.label]
                          : config.default
                      }
                      setValue={(value) => {
                        // Only store in config when user actually changes a value
                        updateConfig(DEFAULT_CONFIG_KEY, config.label, value);
                      }}
                    />
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
