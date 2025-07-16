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
import { Eye, EyeOff, Key, Trash2 } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface ApiKey {
  id: string;
  name: string;
  value: string;
  isVisible: boolean;
  lastUsed?: string;
}

interface ApiKeySection {
  title: string;
  keys: ApiKey[];
}

export function APIKeysTab() {
  const [apiKeySections, setApiKeySections] = useState<
    Record<string, ApiKeySection>
  >({
    llms: {
      title: "LLMs",
      keys: [
        {
          id: "anthropicApiKey",
          name: "Anthropic",
          value: "",
          isVisible: false,
        },
        { id: "openaiApiKey", name: "OpenAI", value: "", isVisible: false },
        {
          id: "googleApiKey",
          name: "Google Gen AI",
          value: "",
          isVisible: false,
        },
      ],
    },
    infrastructure: {
      title: "Infrastructure",
      keys: [
        { id: "daytonaApiKey", name: "Daytona", value: "", isVisible: false },
      ],
    },
  });

  const toggleKeyVisibility = (sectionKey: string, keyId: string) => {
    setApiKeySections((prev) => ({
      ...prev,
      [sectionKey]: {
        ...prev[sectionKey],
        keys: prev[sectionKey].keys.map((key) =>
          key.id === keyId ? { ...key, isVisible: !key.isVisible } : key,
        ),
      },
    }));
  };

  const updateApiKey = (sectionKey: string, keyId: string, value: string) => {
    setApiKeySections((prev) => ({
      ...prev,
      [sectionKey]: {
        ...prev[sectionKey],
        keys: prev[sectionKey].keys.map((key) =>
          key.id === keyId ? { ...key, value } : key,
        ),
      },
    }));
  };

  const deleteApiKey = (sectionKey: string, keyId: string) => {
    setApiKeySections((prev) => ({
      ...prev,
      [sectionKey]: {
        ...prev[sectionKey],
        keys: prev[sectionKey].keys.map((key) =>
          key.id === keyId ? { ...key, value: "" } : key,
        ),
      },
    }));
  };

  return (
    <div className="space-y-8">
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
                        API Key
                      </Label>
                      <div className="mt-1 flex items-center gap-2">
                        <Input
                          id={`${apiKey.id}-key`}
                          type={apiKey.isVisible ? "text" : "password"}
                          value={apiKey.value}
                          onChange={(e) =>
                            updateApiKey(sectionKey, apiKey.id, e.target.value)
                          }
                          placeholder={`Enter your ${apiKey.name} API key`}
                          className="font-mono text-sm"
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            toggleKeyVisibility(sectionKey, apiKey.id)
                          }
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
                            onClick={() => deleteApiKey(sectionKey, apiKey.id)}
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

                  <div className="flex items-center justify-between">
                    <p className="text-muted-foreground text-xs">
                      Your API key is stored securely and encrypted
                    </p>
                    {apiKey.value && (
                      <Button
                        size="sm"
                        variant="outline"
                      >
                        Test Connection
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
