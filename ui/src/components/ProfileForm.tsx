import { useEffect, useState } from "react";

import type { ModelOption, Profile, ProfileUpdate, Repository } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface ProfileFormProps {
  models: Array<ModelOption>;
  repos: Array<Repository>;
  initial: Profile;
  onSubmit: (body: ProfileUpdate) => Promise<unknown>;
  saving: boolean;
  error: string | null;
}

export function ProfileForm({ models, repos, initial, onSubmit, saving, error }: ProfileFormProps) {
  const first: ModelOption | undefined = models[0];
  const [modelId, setModelId] = useState<string>(initial.default_model ?? first?.id ?? "");
  const currentModel: ModelOption | undefined = models.find((m) => m.id === modelId) ?? first;
  const [effort, setEffort] = useState<string>(
    initial.reasoning_effort ?? currentModel?.default_effort ?? "",
  );
  const [defaultRepo, setDefaultRepo] = useState<string>(initial.default_repo ?? "");

  useEffect(() => {
    if (currentModel !== undefined && !currentModel.efforts.includes(effort)) {
      setEffort(currentModel.default_effort);
    }
  }, [modelId, currentModel, effort]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void onSubmit({
      default_model: modelId,
      reasoning_effort: effort,
      default_repo: defaultRepo || null,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Label htmlFor="model">Default model</Label>
        <Select value={modelId} onValueChange={(v) => v && setModelId(v)}>
          <SelectTrigger id="model">
            <SelectValue placeholder="Pick a model" />
          </SelectTrigger>
          <SelectContent>
            {models.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="effort">Reasoning effort</Label>
        <Select value={effort} onValueChange={(v) => v && setEffort(v)}>
          <SelectTrigger id="effort">
            <SelectValue placeholder="Pick an effort level" />
          </SelectTrigger>
          <SelectContent>
            {currentModel?.efforts.map((e) => (
              <SelectItem key={e} value={e}>
                {e}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="repo">Default repo</Label>
        {repos.length > 0 ? (
          <Select
            value={defaultRepo || "__none__"}
            onValueChange={(v) => setDefaultRepo(!v || v === "__none__" ? "" : v)}
          >
            <SelectTrigger id="repo">
              <SelectValue placeholder="Pick a default repo" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">(none)</SelectItem>
              {repos.map((r) => (
                <SelectItem key={r.full_name} value={r.full_name}>
                  {r.full_name}
                  {r.private ? " · private" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            id="repo"
            placeholder="owner/repo"
            value={defaultRepo}
            onChange={(e) => setDefaultRepo(e.target.value)}
          />
        )}
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      <div className="flex justify-end">
        <Button type="submit" disabled={saving || !modelId || !effort}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}
