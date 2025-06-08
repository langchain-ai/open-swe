"use client";
import { forwardRef, ForwardedRef, useState, useEffect } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfigField } from "@/components/configuration-sidebar/config-field";
import { ConfigSection } from "@/components/configuration-sidebar/config-section";
import { useConfigStore } from "@/hooks/use-config-store";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfigurableFieldUIMetadata } from "@/types/configurable";
import { Button } from "@/components/ui/button";
import { PanelRightOpen } from "lucide-react";
import { GraphConfiguration } from "@open-swe/shared/open-swe/types";

/**
 * Extract configuration metadata from the GraphConfiguration Zod schema
 */
function extractConfigurationsFromSchema(): ConfigurableFieldUIMetadata[] {
  const configurations: ConfigurableFieldUIMetadata[] = [];
  const shape = GraphConfiguration.shape;

  for (const [fieldName, fieldSchema] of Object.entries(shape)) {
    // Skip hidden fields
    const metadata = (fieldSchema as any)._def?.x_oap_ui_config;
    if (!metadata || metadata.type === "hidden") {
      continue;
    }

    configurations.push({
      label: fieldName,
      type: metadata.type,
      description: metadata.description,
      options: metadata.options,
      default: metadata.default,
      min: metadata.min,
      max: metadata.max,
      step: metadata.step,
      placeholder: metadata.placeholder,
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

  const [configurations, setConfigurations] = useState<
    ConfigurableFieldUIMetadata[]
  >([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);

    // Extract configurations from the GraphConfiguration Zod schema
    const actualConfigs = extractConfigurationsFromSchema();
    console.log("actualConfigs", actualConfigs);
    // Set Default Configs if they don't exist
    actualConfigs.forEach((config) => {
      if (configs[config.label] === undefined && config.default !== undefined) {
        updateConfig(config.label, config.default);
      }
    });

    setConfigurations(actualConfigs);
    setLoading(false);
  }, [configs, updateConfig]);

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
