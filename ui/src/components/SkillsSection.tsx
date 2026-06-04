import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type { Skill } from "@/lib/api"
import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"

const NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

type Draft = { name: string; description: string; body: string }

const EMPTY_DRAFT: Draft = { name: "", description: "", body: "" }

export function SkillsSection() {
  const qc = useQueryClient()
  const skills = useQuery({ queryKey: ["skills"], queryFn: api.listSkills })

  // `editing` is the name of the skill being edited, "" while creating a new
  // one, or null when the editor is closed.
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [error, setError] = useState<string | null>(null)

  const isNew = editing === ""
  const nameValid = NAME_RE.test(draft.name) && draft.name.length <= 64
  const canSave = nameValid && draft.description.trim().length > 0 && !error

  const close = () => {
    setEditing(null)
    setDraft(EMPTY_DRAFT)
    setError(null)
  }

  const onError = (e: Error) => setError(e.message)
  const onSaved = () => {
    close()
    void qc.invalidateQueries({ queryKey: ["skills"] })
  }

  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api.createSkill(draft)
        : api.updateSkill(editing as string, draft),
    onSuccess: onSaved,
    onError,
  })

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteSkill(name),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["skills"] }),
    onError,
  })

  const startCreate = () => {
    setEditing("")
    setDraft(EMPTY_DRAFT)
    setError(null)
  }

  const startEdit = (skill: Skill) => {
    setEditing(skill.name)
    setDraft({
      name: skill.name,
      description: skill.description,
      body: skill.body,
    })
    setError(null)
  }

  const items = skills.data ?? []

  return (
    <SettingsSection
      title="Skills"
      description="Reusable instructions your agent loads on demand. These layer on top of the shared defaults and override them by name."
      action={
        editing === null ? (
          <Button size="sm" onClick={startCreate}>
            New skill
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3 p-4">
        {editing !== null && (
          <div className="flex flex-col gap-3 rounded-md border border-border bg-background p-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="skill-name">Name</Label>
              <Input
                id="skill-name"
                placeholder="run-tests"
                value={draft.name}
                disabled={!isNew}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
              {draft.name.length > 0 && !nameValid && (
                <span className="text-xs text-destructive">
                  Lowercase letters, numbers and single hyphens only.
                </span>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="skill-description">Description</Label>
              <Input
                id="skill-description"
                placeholder="When and why the agent should use this skill"
                value={draft.description}
                onChange={(e) =>
                  setDraft({ ...draft, description: e.target.value })
                }
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="skill-body">Instructions</Label>
              <Textarea
                id="skill-body"
                className="min-h-[220px] w-full font-mono text-xs"
                placeholder="# Steps\n1. ..."
                value={draft.body}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
              />
            </div>
            {error && <span className="text-xs text-destructive">{error}</span>}
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => save.mutate()}
                disabled={!canSave || save.isPending}
              >
                {save.isPending ? "Saving…" : isNew ? "Create" : "Save"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={close}
                disabled={save.isPending}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        {skills.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : skills.isError ? (
          <p className="text-xs text-destructive">Failed to load skills.</p>
        ) : items.length === 0 && editing === null ? (
          <p className="text-xs text-muted-foreground">
            No skills yet. Create one to teach your agent a reusable procedure.
          </p>
        ) : (
          <div className="flex flex-col gap-0.5">
            {items.map((skill) => (
              <div
                key={skill.name}
                className="flex items-center justify-between gap-2 border-b border-border py-1.5 text-sm last:border-b-0"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">{skill.name}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {skill.description}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => startEdit(skill)}
                    disabled={editing !== null}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove.mutate(skill.name)}
                    disabled={remove.isPending || editing !== null}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </SettingsSection>
  )
}
