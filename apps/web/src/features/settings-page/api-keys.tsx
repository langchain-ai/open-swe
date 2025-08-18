import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Eye,
  EyeOff,
  Key,
  Trash2,
  Info,
  Server,
  CircleQuestionMark,
  Plus,
  AlertTriangle,
} from "lucide-react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useConfigStore, DEFAULT_CONFIG_KEY } from "@/hooks/useConfigStore";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { parseEnvFile } from "@/lib/parse-env";

interface ApiKey {
  id: string;
  name: string;
  description?: string;
  value: string;
  allowedInDev: boolean;
  isVisible: boolean;
  lastUsed?: string;
}

interface ApiKeySection {
  title: string;
  keys: ApiKey[];
}

const API_KEY_SECTIONS: Record<string, Omit<ApiKeySection, "keys">> = {
  llms: {
    title: "LLMs",
  },
  infrastructure: {
    title: "Infrastructure",
  },
  custom: {
    title: "Add More Environment Variables",
  },
};

const PROVIDER_DEFINITIONS = {
  llms: [
    { id: "anthropic", name: "ANTHROPIC_API_KEY", description: "" },
    { id: "openai", name: "OPENAI_API_KEY", description: "" },
    { id: "google", name: "GOOGLE_API_KEY", description: "" },
  ],
  infrastructure: [
    {
      id: "daytona",
      name: "DAYTONA_API_KEY",
      description: "Users not required to set this if using the demo",
    },
  ],
  custom: [],
};

const shouldAutofocus = (apiKeyId: string, hasValue: boolean): boolean => {
  if (apiKeyId === "anthropicApiKey") {
    return !hasValue;
  }

  return false;
};

export function APIKeysTab() {
  const { getConfig, updateConfig } = useConfigStore();
  const config = getConfig(DEFAULT_CONFIG_KEY);

  const [visibilityState, setVisibilityState] = useState<
    Record<string, boolean>
  >({});

  const [editingKeyNames, setEditingKeyNames] = useState<
    Record<string, string>
  >({});

  const toggleKeyVisibility = (providerId: string) => {
    setVisibilityState((prev) => ({
      ...prev,
      [providerId]: !prev[providerId],
    }));
  };

  const updateEnvVarProperty = (
    id: string,
    updates: Partial<{
      name: string;
      apiKey: string;
      allowedInDev: boolean;
    }>,
  ) => {
    const currentApiKeys = config.apiKeys || {};
    const keyData = currentApiKeys[id] || {};

    // Ensure required fields exist for predefined providers
    const predefinedProvider = [
      ...PROVIDER_DEFINITIONS.llms,
      ...PROVIDER_DEFINITIONS.infrastructure,
    ].find((p) => p.id === id);

    const updatedKeyData = { ...keyData, ...updates };
    if (predefinedProvider) {
      if (!updatedKeyData.name) {
        updatedKeyData.name = predefinedProvider.name;
      }
      if (updatedKeyData.allowedInDev === undefined) {
        updatedKeyData.allowedInDev = false;
      }
    }

    updateConfig(DEFAULT_CONFIG_KEY, "apiKeys", {
      ...currentApiKeys,
      [id]: { ...keyData, ...updatedKeyData },
    });
  };

  const deleteApiKey = (id: string) => {
    const currentApiKeys = config.apiKeys || {};
    const updatedApiKeys = { ...currentApiKeys };
    delete updatedApiKeys[id];
    updateConfig(DEFAULT_CONFIG_KEY, "apiKeys", updatedApiKeys);
  };

  const addCustomEnvVar = () => {
    const id = crypto.randomUUID();
    updateEnvVarProperty(id, { name: "", apiKey: "", allowedInDev: false });
  };

  const updateCustomKeyName = (id: string, newName: string) => {
    updateEnvVarProperty(id, { name: newName });
  };

  const handlePasteDetection = (pastedText: string, currentId: string) => {
    try {
      const parsedVars = parseEnvFile(pastedText);

      // If we get multiple variables or it looks like an env format, handle as bulk import
      if (
        parsedVars.length > 1 ||
        (parsedVars.length === 1 && pastedText.includes("="))
      ) {
        const currentApiKeys = config.apiKeys || {};
        const newApiKeys = { ...currentApiKeys };

        parsedVars.forEach((envVar, index) => {
          if (index === 0) {
            newApiKeys[currentId] = {
              ...newApiKeys[currentId],
              name: envVar.name,
              apiKey: envVar.value,
            };
          } else {
            const id = crypto.randomUUID();
            newApiKeys[id] = {
              name: envVar.name,
              apiKey: envVar.value,
              allowedInDev: false,
            };
          }
        });

        updateConfig(DEFAULT_CONFIG_KEY, "apiKeys", newApiKeys);
        return true;
      }
    } catch {
      // Not a valid env format, treat as regular text
    }

    return false;
  };

  const getApiKeySections = (): Record<string, ApiKeySection> => {
    const sections: Record<string, ApiKeySection> = {};
    const currentApiKeys = config.apiKeys || {};

    const predefinedEnvVarIds = [
      ...PROVIDER_DEFINITIONS.llms.map((p) => p.id),
      ...PROVIDER_DEFINITIONS.infrastructure.map((p) => p.id),
    ];

    const customEnvVars = Object.entries(currentApiKeys)
      .filter(([envId, envData]: [string, any]) => {
        return !predefinedEnvVarIds.includes(envId);
      })
      .map(([envId, envData]: [string, any]) => ({
        id: envId,
        name: envData.name,
        description: "",
      }));

    const dynamicProviderDefinitions = {
      ...PROVIDER_DEFINITIONS,
      custom: customEnvVars,
    };

    Object.entries(API_KEY_SECTIONS).forEach(([sectionKey, sectionInfo]) => {
      sections[sectionKey] = {
        ...sectionInfo,
        keys: dynamicProviderDefinitions[
          sectionKey as keyof typeof dynamicProviderDefinitions
        ].map((providerDef): ApiKey => {
          const providerData = currentApiKeys[providerDef.id] || {};
          return {
            id: providerDef.id,
            name: providerData.name || providerDef.name,
            description: providerDef.description,
            value: providerData.apiKey || "",
            allowedInDev: providerData.allowedInDev || false,
            isVisible: visibilityState[providerDef.id] || false,
            lastUsed: providerData.lastUsed,
          };
        }),
      };
    });

    return sections;
  };

  const apiKeySections = getApiKeySections();

  // Get all keys that are exposed to dev server
  const exposedKeys = Object.values(apiKeySections)
    .flatMap((section) => section.keys)
    .filter((key) => key.allowedInDev && key.value)
    .map((key) => key.name);

  return (
    <div className="space-y-8">
      <Alert className="border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-900/20">
        <Info className="h-4 w-4 text-blue-600 dark:text-blue-400" />
        <AlertDescription className="text-blue-800 dark:text-blue-300">
          <p>
            Open SWE uses Anthropic models by default. Configure your Anthropic
            API key below to get started.
          </p>
          <p>Only an Anthropic API key is required to get started.</p>
        </AlertDescription>
      </Alert>

      {exposedKeys.length > 0 && (
        <Alert className="border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-400">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            <strong>Warning:</strong> The following environment variables are
            exposed to the development sandbox:{" "}
            {exposedKeys.map((keyName, index) => (
              <span key={keyName}>
                <code className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-xs text-amber-800 dark:bg-amber-800/20 dark:text-amber-200">
                  {keyName}
                </code>
                {index < exposedKeys.length - 1 && ", "}
              </span>
            ))}
            <p>
              Your API keys will be readable by LLMs and any code running in the
              sandbox environment. We only recommend enabling this feature if
              you understand the security implications.
            </p>
            <p>
              For more information, see our{" "}
              <a
                href="https://docs.langchain.com/labs/swe/security#sandbox-environment-variables"
                target="_blank"
                rel="noopener noreferrer"
              >
                security docs
              </a>
              .
            </p>
          </AlertDescription>
        </Alert>
      )}

      {Object.entries(apiKeySections).map(([sectionKey, section]) => (
        <Card
          key={sectionKey}
          className="bg-card border-border shadow-sm"
        >
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-xl">
              <Key className="h-5 w-5" />
              {section.title}
            </CardTitle>
            <CardDescription>
              {sectionKey === "custom"
                ? "Add custom environment variables for use in the development sandbox"
                : `Manage API keys for ${section.title.toLowerCase()} services`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {section.keys.map((apiKey: ApiKey, index: number) => (
              <div
                key={apiKey.id}
                className="border-border rounded-lg border p-4"
              >
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {sectionKey === "custom" ? (
                      <Input
                        value={editingKeyNames[apiKey.id] ?? apiKey.name}
                        onChange={(e) =>
                          setEditingKeyNames((prev) => ({
                            ...prev,
                            [apiKey.id]: e.target.value,
                          }))
                        }
                        onPaste={(e) => {
                          const pastedText = e.clipboardData.getData("text");
                          if (handlePasteDetection(pastedText, apiKey.id)) {
                            e.preventDefault();
                            setEditingKeyNames((prev) => {
                              const updated = { ...prev };
                              delete updated[apiKey.id];
                              return updated;
                            });
                          }
                        }}
                        onBlur={(e) => {
                          const cleanKey = e.target.value
                            .trim()
                            .toUpperCase()
                            .replace(/[^A-Z0-9_]/g, "_");
                          if (cleanKey !== apiKey.name) {
                            updateCustomKeyName(apiKey.id, cleanKey);
                          }
                          // Clear the editing state
                          setEditingKeyNames((prev) => {
                            const updated = { ...prev };
                            delete updated[apiKey.id];
                            return updated;
                          });
                        }}
                        placeholder="VARIABLE_NAME"
                        className="text-foreground h-auto w-auto min-w-[200px] border-dashed bg-transparent px-2 py-1 font-mono text-base font-semibold"
                      />
                    ) : (
                      <h3 className="text-foreground font-mono font-semibold">
                        {apiKey.name}
                      </h3>
                    )}
                    {apiKey.value && (
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-xs",
                          "border-green-200 bg-green-50 text-green-700",
                          "dark:border-green-800 dark:bg-green-900/20 dark:text-green-400",
                        )}
                      >
                        Configured
                      </Badge>
                    )}
                    {apiKey.lastUsed && (
                      <span className="text-muted-foreground text-xs">
                        Last used {apiKey.lastUsed}
                      </span>
                    )}
                  </div>
                </div>

                <div className="space-y-3">
                  {/* API Key Input Section */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1">
                      <Label
                        htmlFor={`${apiKey.id}-key`}
                        className="text-sm font-medium"
                      >
                        {sectionKey === "custom" ? "Value" : "API Key"}
                      </Label>
                      {apiKey.description && (
                        <p className="text-muted-foreground text-xs">
                          {apiKey.description}
                        </p>
                      )}
                      <div className="mt-1 flex items-center gap-2">
                        <Input
                          id={`${apiKey.id}-key`}
                          type={apiKey.isVisible ? "text" : "password"}
                          value={apiKey.value}
                          onChange={(e) =>
                            updateEnvVarProperty(apiKey.id, {
                              apiKey: e.target.value,
                            })
                          }
                          placeholder={
                            sectionKey === "custom"
                              ? `Enter value for ${apiKey.name}`
                              : `Enter your ${apiKey.name}`
                          }
                          className="font-mono text-sm"
                          autoFocus={shouldAutofocus(apiKey.id, !!apiKey.value)}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleKeyVisibility(apiKey.id)}
                          className="px-2"
                        >
                          {apiKey.isVisible ? (
                            <EyeOff className="h-4 w-4" />
                          ) : (
                            <Eye className="h-4 w-4" />
                          )}
                        </Button>
                        {(apiKey.value || sectionKey === "custom") && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => deleteApiKey(apiKey.id)}
                            className={cn(
                              "px-2",
                              "text-destructive hover:bg-destructive/10 hover:text-destructive",
                            )}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Dev Server Toggle */}
                  <div className="border-border flex items-center justify-between border-t pt-2">
                    <div className="flex items-center gap-1">
                      <Label
                        htmlFor={`${apiKey.id}-devserver`}
                        className="text-sm font-medium"
                      >
                        <Server className="text-muted-foreground h-4 w-4" />
                        Include in Dev Server
                      </Label>
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <CircleQuestionMark className="text-muted-foreground h-3 w-3 cursor-help" />
                          </TooltipTrigger>
                          <TooltipContent>
                            <p className="max-w-xs">
                              Expose this{" "}
                              {sectionKey === "custom"
                                ? "environment variable"
                                : "API key"}{" "}
                              in the development sandbox
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                    <Switch
                      id={`${apiKey.id}-devserver`}
                      checked={apiKey.allowedInDev}
                      onCheckedChange={(enabled) =>
                        updateEnvVarProperty(apiKey.id, {
                          allowedInDev: enabled,
                        })
                      }
                      disabled={!apiKey.value}
                    />
                  </div>
                </div>
              </div>
            ))}

            {/* Button for custom section */}
            {sectionKey === "custom" && (
              <div className="flex justify-center pt-2">
                <Button
                  variant="outline"
                  onClick={addCustomEnvVar}
                  className="hover:bg-accent h-10 min-w-[200px] gap-2 border-2 border-dashed transition-all hover:border-solid"
                >
                  <Plus className="h-4 w-4" />
                  Add Environment Variable
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
