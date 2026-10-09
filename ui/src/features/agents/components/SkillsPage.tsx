import { Button } from "@langchain/macaw-components/Button"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useEffect, useState } from "react"

import type { Skill } from "@/lib/api"
import { EditorScreen } from "@/components/EditorScreen"
import { TextEditor } from "@/components/TextEditor"
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

  const dirty =
    selected != null &&
    (draft.description !== selected.description ||
      draft.instructions !== selected.instructions)
  const creating = selectedName === null

  return (
    <EditorScreen
      title="Skills"
      description="Reusable instructions Open SWE loads when a task matches their description."
      actions={
        <>
          {error && (
            <span className="text-xs text-error-secondary">{error}</span>
          )}
          {dirty && (
            <span className="text-xs text-secondary">Unsaved changes</span>
          )}
          {canEdit && !creating && (
            <Button
              size="xs"
              color="error"
              disabled={remove.isPending}
              onClick={onDelete}
            >
              Delete
            </Button>
          )}
          {canEdit && (
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
          )}
        </>
      }
      sidebar={
        <>
          <GroupedTabs<Scope>
            size="xs"
            className="w-fit"
            value={organization ? "organization" : "personal"}
            onChange={(scope) => selectScope(scope === "organization")}
            options={SCOPES}
          />
          {canEdit && (
            <Button size="xs" className="w-full" onClick={clear}>
              New skill
            </Button>
          )}
          <div className="space-y-1">
            {skills.isLoading && <Skeleton className="h-24" />}
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
        </>
      }
    >
      {!canEdit && !selected ? (
        <p className="m-auto p-6 text-xs text-secondary">
          Select an organization skill to view it.
        </p>
      ) : (
        <>
          <div className="grid gap-4 border-b border-default p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
            <Input
              id="skill-name"
              label="Name"
              size="md"
              value={selectedName ?? newName}
              onChange={setNewName}
              disabled={!creating}
              placeholder="address-review-feedback"
              hintText={
                creating
                  ? "Lowercase letters, numbers, and single hyphens."
                  : undefined
              }
            />
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
          </div>
          <TextEditor
            value={draft.instructions}
            onChange={(instructions) =>
              setDraft((value) => ({ ...value, instructions }))
            }
            ariaLabel="Skill instructions"
            disabled={!canEdit}
            placeholder="Write the skill workflow in Markdown."
          />
        </>
      )}
    </EditorScreen>
  )
}
