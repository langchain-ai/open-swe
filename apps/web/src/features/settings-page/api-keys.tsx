import { useState } from "react";
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
import { Eye, EyeOff, Key, Trash2, Info } from "lucide-react";
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
  fields?: ConfigField[];
}

interface ApiKeySection {
  title: string;
  keys: ApiKey[];
}

const API_KEY_SECTIONS: Record<string, Omit<ApiKeySection, "keys">> = {
  llms: {
    title: "LLMs",
  },
  // infrastructure: {
  //   title: "Infrastructure",
  // },
};

interface ConfigField {
  id: string;
  name: string;
  description?: string;
  value: string;
  isVisible: boolean;
}

const AWS_BEDROCK_FIELDS: ConfigField[] = [
  {
    id: "awsAccessKeyId",
    name: "Access Key ID",
    description: "Your AWS access key for authenticating with Bedrock",
    value: "",
    isVisible: false,
  },
  {
    id: "awsSecretAccessKey",
    name: "Secret Access Key",
    description: "Your AWS secret key for authenticating with Bedrock",
    value: "",
    isVisible: false,
  },
  {
    id: "awsRegion",
    name: "Region",
    description: "The AWS region where Bedrock is enabled",
    value: "",
    isVisible: true,
  },
];

const FIELD_GROUPS: Record<string, ConfigField[]> = {
  awsBedrock: AWS_BEDROCK_FIELDS,
};

const API_KEY_DEFINITIONS = {
  llms: [
    { id: "anthropicApiKey", name: "Anthropic" },
    { id: "openaiApiKey", name: "OpenAI" },
    { id: "googleApiKey", name: "Google Gen AI" },
    { id: "awsBedrock", name: "AWS Bedrock", fieldGroup: "awsBedrock" },
  ],
};

const shouldAutofocus = (apiKeyId: string, hasValue: boolean): boolean => {
  if (apiKeyId === "anthropicApiKey") {
    return !hasValue;
  }

  return false;
};

const isProviderConfigured = (apiKey: ApiKey, apiKeys: Record<string, string>): boolean => {
  if (apiKey.fields) {
    const requiredFields = apiKey.fields.filter(field => 
      !field.name.toLowerCase().includes('optional')
    );
    return requiredFields.every(field => apiKeys[field.id]);
  }

  return !!apiKey.value;
};

export function APIKeysTab() {
  const { getConfig, updateConfig } = useConfigStore();
  const config = getConfig(DEFAULT_CONFIG_KEY);

  const [visibilityState, setVisibilityState] = useState<
    Record<string, boolean>
  >({});

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

  const deleteApiKey = (keyId: string) => {
    const currentApiKeys = config.apiKeys || {};
    const updatedApiKeys = { ...currentApiKeys };
    delete updatedApiKeys[keyId];
    updateConfig(DEFAULT_CONFIG_KEY, "apiKeys", updatedApiKeys);
  };

  const getApiKeySections = (fieldGroups: Record<string, ConfigField[]>): Record<string, ApiKeySection> => {
    const sections: Record<string, ApiKeySection> = {};
    const apiKeys = config.apiKeys || {};

    Object.entries(API_KEY_SECTIONS).forEach(([sectionKey, sectionInfo]) => {
      sections[sectionKey] = {
        ...sectionInfo,
        keys: API_KEY_DEFINITIONS[
          sectionKey as keyof typeof API_KEY_DEFINITIONS
        ].map((keyDef) => ({
          ...keyDef,
          value: keyDef.fieldGroup ? "" : (apiKeys[keyDef.id] || ""),
          isVisible: visibilityState[keyDef.id] || false,
          fields: keyDef.fieldGroup ? fieldGroups[keyDef.fieldGroup] : undefined,
        })),
      };
    });

    return sections;
  };

  const apiKeys = config.apiKeys || {};
  
  const getFieldsWithValues = (fields: ConfigField[]): ConfigField[] => {
    return fields.map((field) => ({
      ...field,
      value: apiKeys[field.id] || "",
      isVisible: visibilityState[field.id] || false,
    }));
  };
  
  const fieldGroupsWithValues: Record<string, ConfigField[]> = {};
  Object.entries(FIELD_GROUPS).forEach(([groupId, fields]) => {
    fieldGroupsWithValues[groupId] = getFieldsWithValues(fields);
  });
  
  const apiKeySections = getApiKeySections(fieldGroupsWithValues);

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
              Manage API keys for {section.title.toLowerCase()} services
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
                    {isProviderConfigured(apiKey, apiKeys) && (
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
                  {apiKey.fields ? (
                    apiKey.fields.map((field) => (
                      <div key={field.id} className="space-y-2">
                        <Label
                          htmlFor={field.id}
                          className="text-sm font-medium"
                        >
                          {field.name}
                        </Label>
                        {field.description && (
                          <p className="text-xs text-muted-foreground">
                            {field.description}
                          </p>
                        )}
                        <div className="flex items-center gap-2">
                          <Input
                            id={field.id}
                            type={field.isVisible ? "text" : "password"}
                            value={field.value || ""}
                            onChange={(e) => updateApiKey(field.id, e.target.value)}
                            placeholder={`Enter your ${field.name}`}
                            className="font-mono text-sm"
                          />
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => toggleKeyVisibility(field.id)}
                            className="px-2"
                          >
                            {field.isVisible ? (
                              <EyeOff className="h-4 w-4" />
                            ) : (
                              <Eye className="h-4 w-4" />
                            )}
                          </Button>
                          {field.value && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => deleteApiKey(field.id)}
                              className={cn(
                                "px-2",
                                "text-destructive hover:bg-destructive/10 hover:text-destructive",
                              )}
                              type="button"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="flex items-center gap-2">
                      <div className="flex-1">
                        <Label
                          htmlFor={`${apiKey.id}-key`}
                          className="text-sm font-medium"
                        >
                          API Key
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
                              updateApiKey(apiKey.id, e.target.value)
                            }
                            placeholder={`Enter your ${apiKey.name} API key`}
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
                        </div>
                      </div>
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
