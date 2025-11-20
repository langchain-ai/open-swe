"use client";

import { useState } from "react";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { DEFAULT_CONFIG_KEY, useConfigStore } from "@/hooks/useConfigStore";
import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import _ from "lodash";
import { cn } from "@/lib/utils";
import { useQueryState } from "nuqs";
import { BasicMarkdownText } from "../thread/markdown-text";

interface Option {
  label: string;
  value: string;
}

interface ConfigFieldProps {
  id: string;
  label: string;
  type:
    | "text"
    | "textarea"
    | "number"
    | "switch"
    | "slider"
    | "select"
    | "json";
  description?: string;
  placeholder?: string;
  options?: Option[];
  min?: number;
  max?: number;
  step?: number;
  className?: string;
  // Optional props for external state management
  value?: any;
  setValue?: (value: any) => void;
}

export function ConfigField({
  id,
  label,
  type,
  description,
  placeholder,
  options = [],
  min,
  max,
  step = 1,
  className,
  value: externalValue, // TODO: Rename to avoid conflict
  setValue: externalSetValue, // TODO: Rename to avoid conflict
}: ConfigFieldProps) {
  const store = useConfigStore();
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Determine whether to use external state or Zustand store
  const isExternallyManaged = externalSetValue !== undefined;

  const currentValue = isExternallyManaged
    ? externalValue
    : store.configs[DEFAULT_CONFIG_KEY]?.[id];

  const handleChange = (newValue: any) => {
    setJsonError(null);
    if (isExternallyManaged && externalSetValue) {
      externalSetValue(newValue);
    } else {
      store.updateConfig(DEFAULT_CONFIG_KEY, id, newValue);
    }
  };

  const handleJsonChange = (jsonString: string) => {
    try {
      if (!jsonString.trim()) {
        handleChange(undefined);
        setJsonError(null);
        return;
      }

      JSON.parse(jsonString);

      handleChange(jsonString);
      setJsonError(null);
    } catch {
      if (isExternallyManaged && externalSetValue) {
        externalSetValue(jsonString);
      } else {
        store.updateConfig(DEFAULT_CONFIG_KEY, id, jsonString);
      }
      setJsonError("Invalid JSON format");
    }
  };

  const handleFormatJson = (jsonString: string) => {
    try {
      const parsed = JSON.parse(jsonString);
      const formattedJson = JSON.stringify(parsed, null, 2);
      handleChange(formattedJson);
      setJsonError(null);
    } catch {
      setJsonError("Invalid JSON format");
    }
  };

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center justify-between">
        <Label
          htmlFor={id}
          className="text-foreground font-mono text-lg font-semibold"
        >
          {_.startCase(label)}
        </Label>
        {type === "switch" && (
          <Switch
            id={id}
            checked={!!currentValue}
            onCheckedChange={handleChange}
          />
        )}
      </div>

      {description && (
        <BasicMarkdownText className="text-xs whitespace-pre-line text-gray-500">
          {description}
        </BasicMarkdownText>
      )}

      {type === "text" && (
        <Input
          id={id}
          value={currentValue || ""}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={placeholder}
        />
      )}

      {type === "textarea" && (
        <Textarea
          id={id}
          value={currentValue || ""}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={placeholder}
          className="min-h-[100px]"
        />
      )}

      {type === "number" && (
        <Input
          id={id}
          type="number"
          value={currentValue !== undefined ? currentValue : ""}
          onChange={(e) => {
            const val = e.target.value;
            if (val === "") {
              handleChange(undefined);
            } else {
              const num = Number(val);
              if (!isNaN(num)) {
                handleChange(num);
              }
            }
          }}
          min={min}
          max={max}
          step={step}
        />
      )}

      {type === "slider" && (
        <div className="pt-2">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs text-gray-500">{min ?? ""}</span>
            <span className="text-sm font-medium">
              {currentValue !== undefined
                ? currentValue
                : min !== undefined && max !== undefined
                  ? (min + max) / 2
                  : ""}
            </span>
            <span className="text-xs text-gray-500">{max ?? ""}</span>
          </div>
          <Slider
            id={id}
            value={[
              currentValue !== undefined
                ? currentValue
                : min !== undefined && max !== undefined
                  ? (min + max) / 2
                  : 0,
            ]}
            min={min}
            max={max}
            step={step}
            onValueChange={(vals: number[]) => handleChange(vals[0])}
            disabled={min === undefined || max === undefined}
          />
        </div>
      )}

      {type === "select" && (
        <Select
          value={currentValue ?? ""}
          onValueChange={handleChange}
        >
          <SelectTrigger>
            <SelectValue placeholder={placeholder || "Select an option"} />
          </SelectTrigger>
          <SelectContent>
            {placeholder && (
              <SelectItem
                value=""
                disabled
              >
                {placeholder}
              </SelectItem>
            )}
            {options.map((option) => (
              <SelectItem
                key={option.value}
                value={option.value}
              >
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {type === "json" && (
        <>
          <Textarea
            id={id}
            value={
              typeof currentValue === "string"
                ? currentValue
                : (JSON.stringify(currentValue, null, 2) ?? "")
            }
            onChange={(e) => handleJsonChange(e.target.value)}
            placeholder={placeholder || '{\n  "key": "value"\n}'}
            className={cn(
              "min-h-[120px] font-mono text-sm",
              jsonError &&
                "border-red-500 focus:border-red-500 focus-visible:ring-red-500",
            )}
          />
          <div className="flex w-full items-center justify-between gap-2 pt-1">
            {/* Use items-start */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleFormatJson(currentValue ?? "")}
              disabled={!currentValue || typeof currentValue !== "string"}
              className="mt-1"
            >
              Format
            </Button>
            {jsonError && (
              <Alert
                variant="destructive"
                className="flex items-center gap-2 rounded-md py-1"
              >
                <AlertCircle className="mb-1" />
                <AlertDescription>{jsonError}</AlertDescription>
              </Alert>
            )}
          </div>
        </>
      )}
    </div>
  );
}
