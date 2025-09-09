"use client";

import { useQueryState } from "nuqs";
import { useLocalRepositories } from "@/hooks/useLocalRepositories";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function RepositorySelect() {
  const { repositories, isLoading } = useLocalRepositories("");
  const [repo, setRepo] = useQueryState("repo");

  return (
    <Select
      value={repo ?? undefined}
      onValueChange={setRepo}
      disabled={isLoading}
    >
      <SelectTrigger className="w-full">
        <SelectValue
          placeholder={
            isLoading ? "Loading repositories..." : "Select a repository"
          }
        />
      </SelectTrigger>
      <SelectContent>
        {repositories.map((r) => (
          <SelectItem
            key={r.path}
            value={r.name}
          >
            {r.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
