"use client";

import { forwardRef, ForwardedRef, useState, useEffect } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfigField } from "@/components/configuration-sidebar/config-field";
import { ConfigSection } from "@/components/configuration-sidebar/config-section";
import { useConfigStore, DEFAULT_CONFIG_KEY } from "@/hooks/useConfigStore";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import type { ConfigurableFieldUIMetadata } from "@open-swe/shared/configurable-metadata";
import { Button } from "@/components/ui/button";
import { PanelRightOpen } from "lucide-react";
import { GraphConfigurationMetadata } from "@open-swe/shared/open-swe/types";
import { useQueryState } from "nuqs";
import { useStreamContext } from "@/providers/Stream";

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

export interface AIConfigPanelProps {
  className?: string;
  open: boolean;
  onClose?: () => void;
}

export const ConfigurationSidebar = forwardRef<
  HTMLDivElement,
  AIConfigPanelProps
>(({ className, open, onClose }, ref: ForwardedRef<HTMLDivElement>) => {
  const { configs, updateConfig } = useConfigStore();
  const stream = useStreamContext();

  const [threadId] = useQueryState("threadId");

  const [configurations, setConfigurations] = useState<
    ConfigurableFieldUIMetadata[]
  >([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    const configKey = threadId || DEFAULT_CONFIG_KEY;

    if (threadId) {
      stream.client.threads.get(threadId).then((t) => {
        if (
          !("config" in t) ||
          !(t as any).config ||
          !(t as any).config.configurable
        ) {
          console.error("Thread does not have config key", t);
          return;
        }

        const actualConfigs = extractConfigurationsFromSchema(
          (t.config as any).configurable,
        );
        actualConfigs.forEach((c) => {
          // Always update the config store with either the default values, or the values from the thread.
          updateConfig(configKey, c.label, c.default);
        });

        setConfigurations(actualConfigs);
      });
    } else {
      const actualConfigs = extractConfigurationsFromSchema({});
      actualConfigs.forEach((c) => {
        updateConfig(configKey, c.label, c.default);
      });
      setConfigurations(actualConfigs);
    }
    setLoading(false);
  }, [threadId]);

  return (
    <div
      ref={ref}
      className={cn(
        "fixed top-0 right-0 z-10 h-screen border-l border-gray-200 bg-white shadow-lg transition-all duration-300",
        open ? "w-80 md:w-xl" : "w-0 overflow-hidden border-l-0",
        className,
      )}
    >
      {open && (
        <div className="flex h-full flex-col">
          <div className="flex flex-shrink-0 items-center justify-between border-b border-gray-200 p-4">
            <h2 className="text-lg font-semibold">Agent Configuration</h2>
            {onClose && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onClose}
                className="h-8 w-8 p-0 hover:bg-gray-100"
              >
                <PanelRightOpen className="h-4 w-4" />
              </Button>
            )}
          </div>

          <Tabs
            defaultValue="general"
            className="flex flex-1 flex-col overflow-y-auto"
          >
            <TabsList className="flex-shrink-0 justify-start bg-transparent px-4 pt-2">
              <TabsTrigger value="general">General</TabsTrigger>
            </TabsList>

            <ScrollArea className="flex-1 overflow-y-auto">
              <TabsContent
                value="general"
                className="m-0 p-4"
              >
                <ConfigSection title="Configuration">
                  {loading ? (
                    <div className="space-y-4">
                      <Skeleton className="h-8 w-full" />
                      <Skeleton className="h-8 w-full" />
                      <Skeleton className="h-8 w-full" />
                    </div>
                  ) : (
                    configurations.map(
                      (c: ConfigurableFieldUIMetadata, index: number) => (
                        <ConfigField
                          key={`${c.label}-${index}`}
                          id={c.label}
                          label={c.label}
                          type={
                            c.type === "boolean" ? "switch" : (c.type ?? "text")
                          }
                          description={c.description}
                          placeholder={c.placeholder}
                          options={c.options}
                          min={c.min}
                          max={c.max}
                          step={c.step}
                        />
                      ),
                    )
                  )}
                </ConfigSection>
              </TabsContent>
            </ScrollArea>
          </Tabs>
        </div>
      )}
    </div>
  );
});

ConfigurationSidebar.displayName = "ConfigurationSidebar";
