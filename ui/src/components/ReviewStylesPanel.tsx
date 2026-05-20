import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type {ReviewStyle} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
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
import { Textarea } from "@/components/ui/textarea";
import { ApiError,  api } from "@/lib/api";
import { normalizeRepoFullName } from "@/lib/repo";

function statusVariant(status: ReviewStyle["status"]) {
  switch (status) {
    case "completed":
      return "default" as const;
    case "running":
      return "secondary" as const;
    case "failed":
      return "destructive" as const;
    default:
      return "outline" as const;
  }
}

export function ReviewStylesPanel() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [addRepo, setAddRepo] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");

  const styles = useQuery({
    queryKey: ["reviewStyles"],
    queryFn: api.listReviewStyles,
    refetchInterval: (q) => {
      const hasRunning = (q.state.data ?? []).some((r) => r.status === "running");
      return hasRunning ? 4000 : false;
    },
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
    queryKey: ["reviewStyle", selected],
    queryFn: () => api.getReviewStyle(selected!),
    enabled: !!selected,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 4000 : false),
  });

  useEffect(() => {
    if (detail.data?.custom_prompt != null) {
      setDraftPrompt(detail.data.custom_prompt);
    } else if (detail.data) {
      setDraftPrompt("");
    }
  }, [detail.data?.custom_prompt, detail.data?.full_name]);

  const createStyle = useMutation({
    mutationFn: (full_name: string) => api.createReviewStyle(full_name),
    onSuccess: (record) => {
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] });
      setSelected(record.full_name);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const analyze = useMutation({
    mutationFn: (full_name: string) => api.analyzeReviewStyle(full_name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] });
      void qc.invalidateQueries({ queryKey: ["reviewStyle", selected] });
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const savePrompt = useMutation({
    mutationFn: ({ full_name, custom_prompt }: { full_name: string; custom_prompt: string }) =>
      api.saveReviewStylePrompt(full_name, custom_prompt),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reviewStyles"] });
      void qc.invalidateQueries({ queryKey: ["reviewStyle", selected] });
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (styles.isLoading) {
    return <Skeleton className="h-40" />;
  }

  const configured = new Set((styles.data ?? []).map((s) => s.full_name));
  const suggestedRepos = (repos.data?.repositories ?? []).filter(
    (r) => !configured.has(r.full_name),
  );
  const normalizedAddRepo = normalizeRepoFullName(addRepo);
  const canAdd = normalizedAddRepo !== null && !configured.has(normalizedAddRepo);
  const active = detail.data ?? styles.data?.find((s) => s.full_name === selected) ?? null;

  const handleAdd = () => {
    if (!normalizedAddRepo || !canAdd) return;
    void createStyle.mutateAsync(normalizedAddRepo).then(() => setAddRepo(""));
  };

  return (
    <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-[260px_1fr]">
      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="add-repo">Add repository</Label>
          <Input
            id="add-repo"
            placeholder="owner/repo"
            value={addRepo}
            onChange={(e) => setAddRepo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAdd();
              }
            }}
          />
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
          <Button
            size="sm"
            className="w-full"
            disabled={!canAdd || createStyle.isPending}
            onClick={handleAdd}
          >
            Add
          </Button>
        </div>
        <ul className="space-y-1">
          {(styles.data ?? []).map((s) => (
            <li key={s.full_name}>
              <button
                type="button"
                className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted ${
                  selected === s.full_name ? "bg-muted font-medium" : ""
                }`}
                onClick={() => setSelected(s.full_name)}
              >
                <span className="truncate">{s.full_name}</span>
                <Badge variant={statusVariant(s.status)} className="ml-2 shrink-0">
                  {s.status}
                </Badge>
              </button>
            </li>
          ))}
          {(styles.data ?? []).length === 0 && (
            <li className="px-2 py-1 text-xs text-muted-foreground">No repositories yet.</li>
          )}
        </ul>
      </div>

      <div className="space-y-3">
        {!selected || !active ? (
          <p className="text-xs text-muted-foreground">
            Select a repository on the left to view or edit its review style prompt.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant={statusVariant(active.status)}>{active.status}</Badge>
              {active.top_reviewers.length > 0 && (
                <span className="text-muted-foreground">
                  Reviewers: {active.top_reviewers.join(", ")}
                </span>
              )}
              {active.prs_sampled > 0 && (
                <span className="text-muted-foreground">
                  {active.prs_sampled} PRs · {active.reviews_sampled} reviews sampled
                </span>
              )}
            </div>
            {active.analysis_summary && (
              <p className="text-xs text-muted-foreground">{active.analysis_summary}</p>
            )}
            {active.error && <p className="text-xs text-destructive">{active.error}</p>}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={active.status === "running" || analyze.isPending}
                onClick={() => void analyze.mutateAsync(active.full_name)}
              >
                {active.status === "running" ? "Analyzing…" : "Run analysis"}
              </Button>
              <Button
                size="sm"
                disabled={!draftPrompt.trim() || savePrompt.isPending}
                onClick={() =>
                  void savePrompt.mutateAsync({
                    full_name: active.full_name,
                    custom_prompt: draftPrompt,
                  })
                }
              >
                Save prompt
              </Button>
            </div>
            <Textarea
              className="min-h-[320px] font-mono text-xs"
              value={draftPrompt}
              onChange={(e) => setDraftPrompt(e.target.value)}
              placeholder={
                active.status === "running"
                  ? "Analysis in progress…"
                  : "Run analysis or write a custom prompt for this repository."
              }
              disabled={active.status === "running"}
            />
          </>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
    </div>
  );
}
