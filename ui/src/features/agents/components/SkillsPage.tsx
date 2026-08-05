import { useEffect, useState } from "react"

import type { Skill } from "@/lib/api"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useAgentSkills,
  useCreateAgentSkill,
  useDeleteAgentSkill,
  useUpdateAgentSkill,
} from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"

const EMPTY_DRAFT = { description: "", instructions: "" }

export function SkillsPage() {
  const skills = useAgentSkills()
  const create = useCreateAgentSkill()
  const update = useUpdateAgentSkill()
  const remove = useDeleteAgentSkill()
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [newName, setNewName] = useState("")
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [error, setError] = useState<string | null>(null)

  const selected = skills.data?.find((skill) => skill.name === selectedName)

  useEffect(() => {
    if (selected) {
      setDraft({
        description: selected.description,
        instructions: selected.instructions,
      })
    }
  }, [selected])

  const select = (skill: Skill) => {
    setSelectedName(skill.name)
    setError(null)
  }

  const add = async () => {
    try {
      const skill = await create.mutateAsync({
        name: newName.trim(),
        ...draft,
      })
      setNewName("")
      setSelectedName(skill.name)
      setError(null)
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not create skill"
      )
    }
  }

  const save = async () => {
    if (!selectedName) return
    try {
      await update.mutateAsync({ name: selectedName, ...draft })
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save skill")
    }
  }

  const discardSelection = () => {
    setSelectedName(null)
    setNewName("")
    setDraft(EMPTY_DRAFT)
    setError(null)
  }

  const onDelete = async () => {
    if (
      !selectedName ||
      !window.confirm(`Delete ${selectedName}? This cannot be undone.`)
    ) {
      return
    }
    try {
      await remove.mutateAsync(selectedName)
      discardSelection()
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not delete skill"
      )
    }
  }

  if (skills.isLoading) return <Skeleton className="m-6 h-64 flex-1" />

  const dirty =
    selected != null &&
    (draft.description !== selected.description ||
      draft.instructions !== selected.instructions)
  const creating = selectedName === null

  return (
    <main className="min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-6 py-8">
        <h1 className="font-heading text-base font-medium text-[var(--ui-text)]">
          Skills
        </h1>
        <p className="mt-1 text-xs text-[var(--ui-text-muted)]">
          Reusable instructions Open SWE loads when a task matches their
          description.
        </p>

        <div className="mt-6 grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
          <section>
            <Button size="sm" className="w-full" onClick={discardSelection}>
              New skill
            </Button>
            <div className="mt-3 space-y-1">
              {(skills.data ?? []).map((skill) => (
                <button
                  key={skill.name}
                  type="button"
                  onClick={() => select(skill)}
                  className={cn(
                    "w-full rounded-md px-2.5 py-2 text-left transition-colors",
                    selectedName === skill.name
                      ? "bg-[var(--ui-sidebar-hover)]"
                      : "hover:bg-[var(--ui-sidebar-hover)]"
                  )}
                >
                  <span className="block truncate text-xs font-medium text-[var(--ui-text)]">
                    {skill.name}
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] text-[var(--ui-text-muted)]">
                    {skill.description}
                  </span>
                </button>
              ))}
              {skills.data?.length === 0 && (
                <p className="px-2.5 py-4 text-xs text-[var(--ui-text-muted)]">
                  No skills yet.
                </p>
              )}
            </div>
          </section>

          <section className="space-y-4 rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)] p-4">
            {creating ? (
              <div className="space-y-2">
                <Label htmlFor="skill-name">Name</Label>
                <Input
                  id="skill-name"
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  placeholder="address-review-feedback"
                />
                <p className="text-[10px] text-[var(--ui-text-muted)]">
                  Lowercase letters, numbers, and single hyphens.
                </p>
              </div>
            ) : (
              <p className="text-sm font-medium text-[var(--ui-text)]">
                {selectedName}
              </p>
            )}

            <div className="space-y-2">
              <Label htmlFor="skill-description">Description</Label>
              <Input
                id="skill-description"
                value={draft.description}
                onChange={(event) =>
                  setDraft((value) => ({
                    ...value,
                    description: event.target.value,
                  }))
                }
                placeholder="What this skill does and when Open SWE should use it"
              />
            </div>

            <div className="space-y-2">
              <Label>Instructions</Label>
              <InstructionsEditor
                value={draft.instructions}
                onChange={(instructions) =>
                  setDraft((value) => ({ ...value, instructions }))
                }
                placeholder="Write the skill workflow in Markdown."
              />
            </div>

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={
                  !draft.description.trim() ||
                  (creating
                    ? !newName.trim() || create.isPending
                    : !dirty || update.isPending)
                }
                onClick={() => void (creating ? add() : save())}
              >
                {creating ? "Create skill" : "Save skill"}
              </Button>
              {dirty && (
                <span className="text-xs text-[var(--ui-text-muted)]">
                  Unsaved changes
                </span>
              )}
              {!creating && (
                <Button
                  size="sm"
                  variant="destructive"
                  className="ml-auto"
                  disabled={remove.isPending}
                  onClick={() => void onDelete()}
                >
                  Delete
                </Button>
              )}
            </div>
            {error && (
              <p className="text-xs text-[var(--ui-danger)]">{error}</p>
            )}
          </section>
        </div>
      </div>
    </main>
  )
}
