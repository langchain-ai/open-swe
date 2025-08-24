import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Eye, EyeOff, Key, Trash2, Info, CheckCircle, XCircle, RefreshCw, Check, ChevronDown, ChevronUp } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useConfigStore, DEFAULT_CONFIG_KEY } from "@/hooks/useConfigStore";

interface ApiKey {
  id: string;
  name: string;
  description?: string;
  value: string;
  isVisible: boolean;
  lastUsed?: string;
}

interface ApiKeySection {
  title: string;
  keys: ApiKey[];
}

interface OllamaModel {
  name: string;
  model: string;
  modified_at: string;
  size: number;
  digest: string;
  details: {
    parent_model: string;
    format: string;
    family: string;
    families: string[];
    parameter_size: string;
    quantization_level: string;
  };
}

interface OllamaModelsResponse {
  models: OllamaModel[];
}

const API_KEY_SECTIONS: Record<string, Omit<ApiKeySection, "keys">> = {
  llms: {
    title: "LLMs",
  },
  // infrastructure: {
  //   title: "Infrastructure",
  // },
};

const API_KEY_DEFINITIONS = {
  llms: [
    { id: "anthropicApiKey", name: "Anthropic" },
    { id: "openaiApiKey", name: "OpenAI" },
    { id: "googleApiKey", name: "Google Gen AI" },
    { 
      id: "ollamaBaseUrl", 
      name: "Ollama", 
      description: "Local Ollama service URL (default: http://localhost:11434). No API key required for local usage." 
    },
  ],
  // infrastructure: [
  //   {
  //     id: "daytonaApiKey",
  //     name: "Daytona",
  //     description: "Users not required to set this if using the demo",
  //   },
  // ],
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
  
  // Ollama-specific state
  const [ollamaModels, setOllamaModels] = useState<OllamaModel[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [showModels, setShowModels] = useState(false);

  const toggleKeyVisibility = (keyId: string) => {
    setVisibilityState((prev) => ({
      ...prev,
      [keyId]: !prev[keyId],
    }));
  };

  const updateApiKey = (keyId: string, value: string) => {
    const currentApiKeys = config.apiKeys || {};
    updateConfig(DEFAULT_CONFIG_KEY, "apiKeys", {
      ...currentApiKeys,
      [keyId]: value,
    });
  };

  const fetchOllamaModels = useCallback(async () => {
    const ollamaUrl = config.apiKeys?.ollamaBaseUrl || "http://localhost:11434";
    setIsLoading(true);
    setConnectionError(null);

    try {
      const response = await fetch(`${ollamaUrl}/api/tags`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data: OllamaModelsResponse = await response.json();
      setOllamaModels(data.models);
      setIsConnected(true);
      
      // Initialize selected models if none are set
      if (selectedModels.length === 0 && data.models.length > 0) {
        setSelectedModels([data.models[0].name]);
      }
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : "Connection failed");
      setIsConnected(false);
      setOllamaModels([]);
    } finally {
      setIsLoading(false);
    }
  }, [config.apiKeys?.ollamaBaseUrl, selectedModels.length]);

  const toggleModelSelection = (modelName: string) => {
    setSelectedModels(prev => {
      const newSelection = prev.includes(modelName) 
        ? prev.filter(name => name !== modelName)
        : [...prev, modelName];
      
      // Save selected models to config
      updateConfig(DEFAULT_CONFIG_KEY, "selectedOllamaModels", newSelection);
      
      return newSelection;
    });
  };

  // Load selected models from config on mount
  useEffect(() => {
    const savedModels = config.selectedOllamaModels || [];
    if (Array.isArray(savedModels)) {
      setSelectedModels(savedModels);
    }
  }, [config.selectedOllamaModels]);

  const deleteApiKey = (keyId: string) => {
    const currentApiKeys = config.apiKeys || {};
    const updatedApiKeys = { ...currentApiKeys };
    delete updatedApiKeys[keyId];
    updateConfig(DEFAULT_CONFIG_KEY, "apiKeys", updatedApiKeys);
  };

  const getApiKeySections = (): Record<string, ApiKeySection> => {
    const sections: Record<string, ApiKeySection> = {};
    const apiKeys = config.apiKeys || {};

    Object.entries(API_KEY_SECTIONS).forEach(([sectionKey, sectionInfo]) => {
      sections[sectionKey] = {
        ...sectionInfo,
        keys: API_KEY_DEFINITIONS[
          sectionKey as keyof typeof API_KEY_DEFINITIONS
        ].map((keyDef) => ({
          ...keyDef,
          value: apiKeys[keyDef.id] || "",
          isVisible: visibilityState[keyDef.id] || false,
        })),
      };
    });

    return sections;
  };

  // Auto-fetch models when Ollama URL changes
  useEffect(() => {
    const ollamaUrl = config.apiKeys?.ollamaBaseUrl;
    if (ollamaUrl && ollamaUrl.trim()) {
      fetchOllamaModels();
    }
  }, [config.apiKeys?.ollamaBaseUrl, fetchOllamaModels]);

  // Initial load - try to connect on mount if default URL might work
  useEffect(() => {
    // Always try to connect on initial load
    fetchOllamaModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only run once on mount

  const apiKeySections = getApiKeySections();

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
              Configure {section.title.toLowerCase()} providers
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {section.keys.map((apiKey) => (
              <div
                key={apiKey.id}
                className="border-border rounded-lg border p-4"
              >
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h3 className="text-foreground font-mono font-semibold">
                      {apiKey.name}
                    </h3>
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
                  <div className="flex items-center gap-2">
                    <div className="flex-1">
                      <Label
                        htmlFor={`${apiKey.id}-key`}
                        className="text-sm font-medium"
                      >
                        {apiKey.id === "ollamaBaseUrl" ? "Server URL" : "API Key"}
                      </Label>
                      {apiKey.description && (
                        <p className="text-muted-foreground text-xs">
                          {apiKey.description}
                        </p>
                      )}
                      <div className="mt-1 flex items-center gap-2">
                        <Input
                          id={`${apiKey.id}-key`}
                          type={apiKey.id === "ollamaBaseUrl" ? "url" : (apiKey.isVisible ? "text" : "password")}
                          value={apiKey.value || (apiKey.id === "ollamaBaseUrl" ? "http://localhost:11434" : "")}
                          onChange={(e) =>
                            updateApiKey(apiKey.id, e.target.value)
                          }
                          placeholder={apiKey.id === "ollamaBaseUrl" ? "http://localhost:11434" : `Enter your ${apiKey.name} API key`}
                          className="font-mono text-sm"
                          autoFocus={shouldAutofocus(apiKey.id, !!apiKey.value)}
                        />
                        {apiKey.id !== "ollamaBaseUrl" && (
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
                        )}
                        {apiKey.value && (
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
                        {apiKey.id === "ollamaBaseUrl" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={fetchOllamaModels}
                            disabled={isLoading}
                            className="px-2"
                          >
                            <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} />
                          </Button>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Ollama-specific sections */}
                  {apiKey.id === "ollamaBaseUrl" && (
                    <div className="mt-4 space-y-3">
                      {/* Connection Status */}
                      <div className="space-y-2">
                        {isConnected ? (
                          <div>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setShowModels(!showModels)}
                              className="h-auto p-2 justify-start text-left w-full"
                            >
                              <CheckCircle className="h-4 w-4 text-green-500 mr-2 flex-shrink-0" />
                              <span className="text-sm text-green-600 dark:text-green-400 flex-1">
                                Connected ({ollamaModels.length} models available)
                              </span>
                              {showModels ? (
                                <ChevronUp className="h-4 w-4 text-muted-foreground" />
                              ) : (
                                <ChevronDown className="h-4 w-4 text-muted-foreground" />
                              )}
                            </Button>
                            
                            {/* Collapsible Models List */}
                            {showModels && (
                              <div className="mt-2 border rounded-md bg-muted/20 p-3">
                                <Label className="text-sm font-medium mb-3 block">
                                  Select models to use in Open SWE:
                                </Label>
                                <div className="space-y-2 max-h-48 overflow-y-auto pr-2">
                                  {ollamaModels.map((model) => (
                                    <div key={model.name} className="flex items-start space-x-3 p-2 rounded-md hover:bg-background/50 transition-colors">
                                      <Checkbox
                                        id={`model-${model.name}`}
                                        checked={selectedModels.includes(model.name)}
                                        onCheckedChange={() => toggleModelSelection(model.name)}
                                        className="mt-1"
                                      />
                                      <div className="flex-1 min-w-0">
                                        <Label
                                          htmlFor={`model-${model.name}`}
                                          className="text-sm font-mono cursor-pointer block truncate"
                                          title={model.name}
                                        >
                                          {model.name}
                                        </Label>
                                        <div className="text-xs text-muted-foreground mt-1 truncate">
                                          {model.details.parameter_size} • {model.details.family} • {model.details.quantization_level}
                                        </div>
                                      </div>
                                      <div className="text-right flex-shrink-0">
                                        <span className="text-xs text-muted-foreground block">
                                          {(model.size / (1024 * 1024 * 1024)).toFixed(1)}GB
                                        </span>
                                        <span className="text-xs text-muted-foreground">
                                          {new Date(model.modified_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                        </span>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                                
                                {selectedModels.length > 0 && (
                                  <div className="mt-3 pt-3 border-t">
                                    <div className="flex items-center gap-1 mb-1">
                                      <Check className="h-3 w-3 text-green-500" />
                                      <span className="text-xs font-medium text-muted-foreground">
                                        Selected ({selectedModels.length}):
                                      </span>
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                      {selectedModels.join(", ")}
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        ) : connectionError ? (
                          <div className="flex items-center gap-2">
                            <XCircle className="h-4 w-4 text-red-500" />
                            <span className="text-sm text-red-600 dark:text-red-400">
                              {connectionError}
                            </span>
                          </div>
                        ) : isLoading ? (
                          <div className="flex items-center gap-2">
                            <RefreshCw className="h-4 w-4 animate-spin text-blue-500" />
                            <span className="text-sm text-blue-600 dark:text-blue-400">
                              Connecting...
                            </span>
                          </div>
                        ) : null}
                      </div>

                      {/* No models found */}
                      {isConnected && ollamaModels.length === 0 && (
                        <Alert className="border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-400">
                          <Info className="h-4 w-4" />
                          <AlertDescription>
                            No models found. Install models using: <code>ollama pull model-name</code>
                          </AlertDescription>
                        </Alert>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
