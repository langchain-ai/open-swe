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

// Model options based on the actual GraphConfiguration schema
const MODEL_OPTIONS = [
  {
    label: "Claude Sonnet 4 (Extended Thinking)",
    value: "anthropic:extended-thinking:claude-sonnet-4-0",
  },
  {
    label: "Claude Opus 4 (Extended Thinking)",
    value: "anthropic:extended-thinking:claude-opus-4-0",
  },
  {
    label: "Claude Sonnet 4",
    value: "anthropic:claude-sonnet-4-0",
  },
  {
    label: "Claude Opus 4",
    value: "anthropic:claude-opus-4-0",
  },
  {
    label: "Claude 3.7 Sonnet",
    value: "anthropic:claude-3-7-sonnet-latest",
  },
  {
    label: "Claude 3.5 Sonnet",
    value: "anthropic:claude-3-5-sonnet-latest",
  },
  {
    label: "o4",
    value: "openai:o4",
  },
  {
    label: "o4 mini",
    value: "openai:o4-mini",
  },
  {
    label: "o3",
    value: "openai:o3",
  },
  {
    label: "o3 mini",
    value: "openai:o3-mini",
  },
  {
    label: "GPT 4o",
    value: "openai:gpt-4o",
  },
  {
    label: "GPT 4.1",
    value: "openai:gpt-4.1",
  },
  {
    label: "Gemini 2.5 Pro Preview",
    value: "google-genai:gemini-2.5-pro-preview-05-06",
  },
  {
    label: "Gemini 2.5 Flash Preview",
    value: "google-genai:gemini-2.5-flash-preview-05-20",
  },
];

const MODEL_OPTIONS_NO_THINKING = MODEL_OPTIONS.filter(
  ({ value }) =>
    !value.includes("extended-thinking") && !value.startsWith("openai:o"),
);

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

    // These match the x_oap_ui_config metadata from the GraphConfiguration schema
    const actualConfigs: ConfigurableFieldUIMetadata[] = [
      {
        label: "plannerModelName",
        type: "select",
        description: "The model to use for planning",
        options: MODEL_OPTIONS_NO_THINKING,
        default: "anthropic:claude-sonnet-4-0",
      },
      {
        label: "plannerContextModelName",
        type: "select",
        description: "The model to use for planning",
        options: MODEL_OPTIONS,
        default: "anthropic:claude-sonnet-4-0",
      },
      {
        label: "actionGeneratorModelName",
        type: "select",
        description: "The model to use for action generation",
        options: MODEL_OPTIONS,
        default: "anthropic:claude-sonnet-4-0",
      },
      {
        label: "progressPlanCheckerModelName",
        type: "select",
        description: "The model to use for progress plan checking",
        options: MODEL_OPTIONS_NO_THINKING,
        default: "anthropic:claude-sonnet-4-0",
      },
      {
        label: "summarizerModelName",
        type: "select",
        description:
          "The model to use for summarizing the conversation history",
        options: MODEL_OPTIONS_NO_THINKING,
        default: "anthropic:claude-sonnet-4-0",
      },
      {
        label: "maxContextActions",
        type: "number",
        description:
          "Maximum number of context gathering actions during planning",
        min: 1,
        max: 20,
        default: 6,
      },
      {
        label: "maxTokens",
        type: "number",
        description:
          "The maximum number of tokens to generate in an individual generation",
        min: 1,
        max: 64000,
        default: 10000,
      },
    ];

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
