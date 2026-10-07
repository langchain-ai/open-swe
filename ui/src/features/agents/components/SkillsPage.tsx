import { useEffect, useState } from "react"
import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  FormField,
  FormStack,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageFrame } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  ListSidebarChrome,
  ListSidebarTitle,
  SplitView,
} from "@langchain/gtm-platform-design-system/patterns/split-view"
import { SidebarTreeRow } from "@langchain/gtm-platform-design-system/patterns/sidebar-tree"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { FadeText } from "@langchain/gtm-platform-design-system/ui/fade-text"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"

import type { Skill } from "@/lib/api"
import { AlertTriangle, Plus, Sparkles } from "@/components/glyphs"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import {
  useCreateAgentSkill,
  useDeleteAgentSkill,
  useOrganizationAgentSkills,
  usePersonalAgentSkills,
  useUpdateAgentSkill,
} from "@/features/agents/lib/queries"
import { useSession } from "@/lib/session"

const EMPTY_DRAFT = { description: "", instructions: "" }

type SkillScope = "personal" | "organization"

function isSkillScope(value: unknown): value is SkillScope {
  return value === "personal" || value === "organization"
}

/** Skills as a list beside the one being edited: list + work, never a third pane. */
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

  const dirty =
    selected != null &&
    (draft.description !== selected.description ||
      draft.instructions !== selected.instructions)
  const creating = selectedName === null

  const listPane = (
    <Stack className="h-full min-h-0">
      <ListSidebarChrome>
        <Inline gap="sm" align="center" justify="between">
          <ListSidebarTitle icon={Sparkles}>Skills</ListSidebarTitle>
          {canEdit && (
            <Button variant="outline" size="compact" onClick={clear}>
              <Icon icon={Plus} size="sm" />
              New skill
            </Button>
          )}
        </Inline>
        <ToggleGroup
          aria-label="Skill scope"
          value={[organization ? "organization" : "personal"]}
          onValueChange={(next) => {
            const scope = next[0]
            if (isSkillScope(scope)) selectScope(scope === "organization")
          }}
        >
          <ToggleGroupItem value="personal">Personal</ToggleGroupItem>
          <ToggleGroupItem value="organization">Organization</ToggleGroupItem>
        </ToggleGroup>
      </ListSidebarChrome>
      <ScrollArea overflow="vertical" className="min-h-0 flex-1">
        {skills.isLoading ? (
          <Stack gap="sm" padding="md">
            <Skeleton className="h-row-record w-full" />
            <Skeleton className="h-row-record w-full" />
          </Stack>
        ) : skills.data?.length === 0 ? (
          <Box padding="md">
            <EmptyState icon={Sparkles} title="No skills yet." />
          </Box>
        ) : (
          <Stack gap="none" className="gap-0.5 px-3 pb-3">
            {(skills.data ?? []).map((skill) => (
              <SidebarTreeRow
                key={skill.name}
                render={<button type="button" />}
                selected={selectedName === skill.name}
                aria-current={selectedName === skill.name ? "true" : undefined}
                multiline
                onClick={() => select(skill)}
                className="cursor-pointer text-left"
              >
                <Box
                  render={<span />}
                  className="truncate text-label font-medium text-ink"
                >
                  {skill.name}
                </Box>
                <FadeText
                  render={<span />}
                  lines={1}
                  className="text-meta whitespace-nowrap text-ink-subtle"
                >
                  {skill.description}
                </FadeText>
              </SidebarTreeRow>
            ))}
          </Stack>
        )}
      </ScrollArea>
    </Stack>
  )

  const workPane =
    !canEdit && !selected ? (
      <Box className="px-4 py-6 lg:px-6">
        <EmptyState
          icon={Sparkles}
          title="Select an organization skill to view it."
        />
      </Box>
    ) : (
      <Box className="px-4 lg:px-6">
        <PageFrame
          title={creating ? "New skill" : (selectedName ?? "")}
          description="Reusable instructions Open SWE loads when a task matches their description."
          dangerZone={
            canEdit && selectedName ? (
              <ConfirmableAction
                title={`Delete ${selectedName}?`}
                description="This cannot be undone."
                confirmLabel="Delete skill"
                onConfirm={async () => {
                  await remove.mutateAsync(selectedName)
                  clear()
                }}
                trigger={
                  <Button
                    variant="outline"
                    className="border-risk text-risk hover:bg-risk-bg"
                  >
                    Delete
                  </Button>
                }
              />
            ) : undefined
          }
        >
          <FormStack>
            {creating && (
              <FormField
                label="Name"
                help="Lowercase letters, numbers, and single hyphens."
                control={
                  <Input
                    value={newName}
                    onChange={(event) => setNewName(event.target.value)}
                    placeholder="address-review-feedback"
                  />
                }
              />
            )}
            <FormField
              label="Description"
              control={
                <Input
                  value={draft.description}
                  onChange={(event) =>
                    setDraft((value) => ({
                      ...value,
                      description: event.target.value,
                    }))
                  }
                  disabled={!canEdit}
                  placeholder="What this skill does and when Open SWE should use it"
                />
              }
            />
            <FormField
              label="Instructions"
              control={
                <InstructionsEditor
                  value={draft.instructions}
                  onChange={(instructions) =>
                    setDraft((value) => ({ ...value, instructions }))
                  }
                  disabled={!canEdit}
                  placeholder="Write the skill workflow in Markdown."
                />
              }
            />
            {error && (
              <StateNotice
                tone="RISK"
                icon={AlertTriangle}
                title={
                  creating
                    ? "The skill wasn't created"
                    : "The skill wasn't saved"
                }
                description={error}
              />
            )}
            {canEdit && (
              <Inline gap="md" align="center">
                <Button
                  disabled={
                    !draft.description.trim() ||
                    (creating ? !newName.trim() : !dirty)
                  }
                  loading={creating ? create.isPending : update.isPending}
                  onClick={() => void (creating ? add() : save())}
                >
                  {creating ? "Create skill" : "Save skill"}
                </Button>
                {dirty && (
                  <Box render={<span />} className="text-meta text-ink-subtle">
                    Unsaved changes
                  </Box>
                )}
              </Inline>
            )}
          </FormStack>
        </PageFrame>
      </Box>
    )

  return (
    <Box className="h-full min-h-0 w-full min-w-0">
      <SplitView
        listLabel="Skills"
        listPane={listPane}
        workLabel={creating ? "New skill" : (selectedName ?? "Skill")}
        workPane={workPane}
      />
    </Box>
  )
}
