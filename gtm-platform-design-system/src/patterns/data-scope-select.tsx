"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import type { DataScope } from "../lib/data-scope";

interface DataScopeSelectProps {
  value: DataScope;
  onValueChange: (scope: DataScope) => void;
  myLabel?: string;
  allLabel?: string;
}

/** The explicit caller-versus-workspace control used by split list panes. */
function DataScopeSelect({
  allLabel = "All accounts",
  myLabel = "My accounts",
  onValueChange,
  value,
}: DataScopeSelectProps) {
  return (
    <Select
      value={value}
      onValueChange={(next) => onValueChange(next === "ALL" ? "ALL" : "MY")}
    >
      <SelectTrigger aria-label="Data scope" className="w-full">
        <SelectValue>{value === "MY" ? myLabel : allLabel}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="MY">{myLabel}</SelectItem>
        <SelectItem value="ALL">{allLabel}</SelectItem>
      </SelectContent>
    </Select>
  );
}

export { DataScopeSelect };
