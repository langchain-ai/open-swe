import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { InstructionsEditor } from "@/components/InstructionsEditor";
import { ApiError, api, isGithubReauthError, loginUrl } from "@/lib/api";
import { normalizeRepoFullName } from "@/lib/repo";

function formatMutationError(e: Error): string {
  return isGithubReauthError(e)
    ? "GitHub token expired — sign in again using the link above."
    : e.message;
}

export function AgentInstructionsPanel() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [addRepo, setAddRepo] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const instructions = useQuery({
    queryKey: ["agentInstructions"],
    queryFn: api.listAgentInstructions,
  });

  const repos = useQuery({
    queryKey: ["repos"],
    queryFn: async () => {
      try {
        return await api.repos();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          return { installations: [], repositories: [] };
        throw e;
      }
    },
  });

  const detail = useQuery({
    queryKey: ["agentInstruction", selected],
    queryFn: () => api.getAgentInstructions(selected!),
    enabled: !!selected,
  });

  useEffect(() => {
    if (detail.data) setDraft(detail.data.instructions ?? "");
  }, [detail.data?.instructions, detail.data?.full_name]);

  const create = useMutation({
    mutationFn: (full_name: string) => api.createAgentInstructions(full_name),
    onSuccess: (record) => {
      void qc.invalidateQueries({ queryKey: ["agentInstructions"] });
      setSelected(record.full_name);
      setError(null);
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  });

  const save = useMutation({
    mutationFn: ({ full_name, value }: { full_name: string; value: string }) =>
      api.saveAgentInstructions(full_name, value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["agentInstructions"] });
      void qc.invalidateQueries({ queryKey: ["agentInstruction", selected] });
      setError(null);
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  });

  const remove = useMutation({
    mutationFn: (full_name: string) => api.deleteAgentInstructions(full_name),
    onSuccess: (_data, full_name) => {
      void qc.invalidateQueries({ queryKey: ["agentInstructions"] });
      if (selected === full_name) {
        setSelected(null);
        setDraft("");
      }
      setError(null);
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  });

  if (instructions.isLoading) {
    return <Skeleton className="h-40" />;
  }

  const configured = new Set((instructions.data ?? []).map((s) => s.full_name));
  const suggestedRepos = (repos.data?.repositories ?? []).filter(
    (r) => !configured.has(r.full_name),
  );
  const normalizedAddRepo = normalizeRepoFullName(addRepo);
  const canAdd = normalizedAddRepo !== null && !configured.has(normalizedAddRepo);
  const active =
    detail.data ?? instructions.data?.find((s) => s.full_name === selected) ?? null;
  const dirty = active != null && draft !== (active.instructions ?? "");

  const handleAdd = () => {
    if (!normalizedAddRepo || !canAdd) return;
    void create
      .mutateAsync(normalizedAddRepo)
      .then(() => setAddRepo(""))
      .catch(() => undefined);
  };

  const githubReauth =
    (repos.isError && isGithubReauthError(repos.error)) ||
    (error !== null && /github token|re-login required/i.test(error));

  return (
    <div className="flex flex-col gap-6 p-4">
      {githubReauth && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          Your GitHub connection expired.{" "}
          <a href={loginUrl()} className="font-medium underline underline-offset-2">
            Sign in with GitHub again
          </a>{" "}
          to list installed repos.
        </div>
      )}
      <section className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="add-instruction-repo">Add repository</Label>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <Input
              id="add-instruction-repo"
              placeholder="owner/repo"
              value={addRepo}
              onChange={(e) => setAddRepo(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAdd();
                }
              }}
              className="sm:flex-1"
            />
            <Button
              size="sm"
              className="shrink-0 sm:w-auto"
              disabled={!canAdd || create.isPending}
              onClick={handleAdd}
            >
              Add
            </Button>
          </div>
          {suggestedRepos.length > 0 && (
            <Combobox
              items={suggestedRepos.map((r) => r.full_name)}
              value={addRepo}
              onValueChange={(v) => setAddRepo(typeof v === "string" ? v : "")}
            >
              <ComboboxInput
                placeholder="Search installed repos…"
                showClear
                className="w-full"
              />
              <ComboboxContent className="min-w-[var(--anchor-width)]">
                <ComboboxList className="max-h-48">
                  <ComboboxEmpty>No matches</ComboboxEmpty>
                  {suggestedRepos.map((r) => (
                    <ComboboxItem key={r.full_name} value={r.full_name}>
                      <span className="truncate">{r.full_name}</span>
                      {r.private && (
                        <span className="ml-auto text-[10px] text-muted-foreground">
                          private
                        </span>
                      )}
                    </ComboboxItem>
                  ))}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium text-foreground">Repositories</p>
          {(instructions.data ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">No repositories yet.</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {(instructions.data ?? []).map((s) => (
                <li key={s.full_name}>
                  <button
                    type="button"
                    className={`inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted ${
                      selected === s.full_name
                        ? "border-primary bg-muted font-medium"
                        : "border-border"
                    }`}
                    onClick={() => setSelected(s.full_name)}
                  >
                    <span className="truncate">{s.full_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <div className="border-t border-border" />

      <section className="space-y-3">
        {!selected || !active ? (
          <p className="text-xs text-muted-foreground">
            Select a repository above to view or edit its custom agent instructions.
          </p>
        ) : (
          <>
            <p className="text-sm font-medium text-foreground">{active.full_name}</p>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={!dirty || save.isPending}
                onClick={() =>
                  void save.mutateAsync({
                    full_name: active.full_name,
                    value: draft,
                  })
                }
              >
                Save instructions
              </Button>
              {dirty && (
                <span className="self-center text-xs text-muted-foreground">
                  Unsaved changes
                </span>
              )}
              <Button
                size="sm"
                variant="destructive"
                className="ml-auto"
                disabled={remove.isPending}
                onClick={() => {
                  if (
                    !window.confirm(
                      `Remove custom instructions for ${active.full_name}? This cannot be undone.`,
                    )
                  ) {
                    return;
                  }
                  void remove.mutateAsync(active.full_name);
                }}
              >
                Remove
              </Button>
            </div>
            <InstructionsEditor
              value={draft}
              onChange={setDraft}
              placeholder="Write custom instructions for the coding agent on this repository (markdown)."
            />
          </>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </section>
    </div>
  );
}
