import { Button } from "@langchain/macaw-components/Button"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"
import { useEffect, useState } from "react"

import type { Skill } from "@/lib/api"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import {
  useCreateAgentSkill,
  useDeleteAgentSkill,
  useOrganizationAgentSkills,
  usePersonalAgentSkills,
  useUpdateAgentSkill,
} from "@/features/agents/lib/queries"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

const EMPTY_DRAFT = { description: "", instructions: "" }

type Scope = "personal" | "organization"
const SCOPES = [
  { value: "personal", display: "Personal" },
  { value: "organization", display: "Organization" },
] as const

export function SkillsPage() {
  const session = useSession()
  const [organization, setOrganization] = useState(false)
  const personalSkills = usePersonalAgentSkills()
  const organizationSkills = useOrganizationAgentSkills()
  const skills = organization ? organizationSkills : personalSkills
  const create = useCreateAgentSkill(organization)
  const update = useUpdateAgentSkill(organization)
  const remove = useDeleteAgentSkill(organization)
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [newName, setNewName] = useState("")
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [error, setError] = useState<string | null>(null)
  const selected = skills.data?.find((skill) => skill.name === selectedName)
  const canEdit = !organization || session.data?.is_admin === true

  useEffect(() => {
    if (selected) {
      // oxlint-disable-next-line react/set-state-in-effect
      setDraft({
        description: selected.description,
        instructions: selected.instructions,
      })
    }
  }, [selected])

  const clear = () => {
    setSelectedName(null)
    setNewName("")
    setDraft(EMPTY_DRAFT)
    setError(null)
  }

  const selectScope = (next: boolean) => {
    setOrganization(next)
    clear()
  }

  const select = (skill: Skill) => {
    setSelectedName(skill.name)
    setError(null)
  }

  const add = async () => {
    try {
      const skill = await create.mutateAsync({ name: newName.trim(), ...draft })
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

  const onDelete = () => {
    if (
      !selectedName ||
      !window.confirm(`Delete ${selectedName}? This cannot be undone.`)
    ) {
      return
    }
    remove.mutate(selectedName, { onSuccess: clear })
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
        <h1 className="font-heading text-base font-medium text-primary">
          Skills
        </h1>
        <p className="mt-1 text-xs text-secondary">
          Reusable instructions Open SWE loads when a task matches their
          description.
        </p>

        <GroupedTabs<Scope>
          size="xs"
          className="mt-4 w-fit"
          value={organization ? "organization" : "personal"}
          onChange={(scope) => selectScope(scope === "organization")}
          options={SCOPES}
        />

        <div className="mt-6 grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
          <section>
            {canEdit && (
              <Button size="xs" className="w-full" onClick={clear}>
                New skill
              </Button>
            )}
            <div className="mt-3 space-y-1">
              {(skills.data ?? []).map((skill) => (
                <button
                  key={skill.name}
                  type="button"
                  onClick={() => select(skill)}
                  className={cn(
                    "w-full rounded-md px-2.5 py-2 text-left transition-colors",
                    selectedName === skill.name
                      ? "bg-selected"
                      : "hover:bg-surface-level-1-hover"
                  )}
                >
                  <span className="block truncate text-xs font-medium text-primary">
                    {skill.name}
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] text-secondary">
                    {skill.description}
                  </span>
                </button>
              ))}
              {skills.data?.length === 0 && (
                <p className="px-2.5 py-4 text-xs text-secondary">
                  No skills yet.
                </p>
              )}
            </div>
          </section>

          <section className="space-y-4 rounded-lg border border-default bg-surface-level-2 p-4">
            {!canEdit && !selected ? (
              <p className="text-xs text-secondary">
                Select an organization skill to view it.
              </p>
            ) : (
              <>
                {creating ? (
                  <Input
                    id="skill-name"
                    label="Name"
                    size="md"
                    value={newName}
                    onChange={setNewName}
                    placeholder="address-review-feedback"
                    hintText="Lowercase letters, numbers, and single hyphens."
                  />
                ) : (
                  <p className="text-sm font-medium text-primary">
                    {selectedName}
                  </p>
                )}

                <Input
                  id="skill-description"
                  label="Description"
                  size="md"
                  value={draft.description}
                  onChange={(description) =>
                    setDraft((value) => ({ ...value, description }))
                  }
                  disabled={!canEdit}
                  placeholder="What this skill does and when Open SWE should use it"
                />

                <div className="space-y-2">
                  <Text variant="sm" weight="medium">
                    Instructions
                  </Text>
                  <InstructionsEditor
                    value={draft.instructions}
                    onChange={(instructions) =>
                      setDraft((value) => ({ ...value, instructions }))
                    }
                    disabled={!canEdit}
                    placeholder="Write the skill workflow in Markdown."
                  />
                </div>

                {canEdit && (
                  <div className="flex items-center gap-2">
                    <Button
                      size="xs"
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
                      <span className="text-xs text-secondary">
                        Unsaved changes
                      </span>
                    )}
                    {!creating && (
                      <Button
                        size="xs"
                        color="error"
                        className="ml-auto"
                        disabled={remove.isPending}
                        onClick={onDelete}
                      >
                        Delete
                      </Button>
                    )}
                  </div>
                )}
                {error && (
                  <p className="text-xs text-error-secondary">{error}</p>
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </main>
  )
}
