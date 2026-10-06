/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AutomationEditor } from "./AutomationEditor"
import { useSession } from "@/lib/session"

const mocks = vi.hoisted(() => ({
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
  workspaces: [
    { slug: "default", name: "Default", repos: [], is_default: true },
    { slug: "oss", name: "OSS", repos: ["acme/oss"], is_default: false },
    { slug: "core", name: "Core", repos: [], is_default: false },
  ],
}))

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useCreateAgentSchedule: () => ({
    error: null,
    isPending: false,
    mutate: mocks.createMutate,
  }),
  useDeleteAgentSchedule: () => ({
    error: null,
    isPending: false,
    mutate: vi.fn(),
  }),
  useUpdateAgentSchedule: () => ({
    error: null,
    isPending: false,
    mutate: mocks.updateMutate,
  }),
  useWorkspaceOptions: () => ({
    data: { default_slug: "default", workspaces: mocks.workspaces },
  }),
}))
vi.mock("@/features/agents/lib/provider/useModelOptions", () => ({
  useModelOptions: () => ({ models: [], defaultSelection: null }),
}))
vi.mock("@/features/automations/lib/useUnsavedChangesWarning", () => ({
  useUnsavedChangesWarning: () => vi.fn(),
}))
vi.mock("@/lib/profile", () => ({
  useRepos: () => ({ data: { repositories: [] } }),
}))
vi.mock("@/lib/session", () => ({
  useSession: vi.fn(),
}))
vi.mock("@/lib/slack-channels", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/slack-channels")>()),
  useSlackChannelDirectory: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
}))
vi.mock("@/features/settings/components/RepoSelector", () => ({
  RepoSelector: ({
    onRepoChange,
    placeholder,
  }: {
    onRepoChange: (repo: string | null) => void
    placeholder: string
  }) => <button onClick={() => onRepoChange("acme/oss")}>{placeholder}</button>,
}))
vi.mock("@/features/automations/components/AutomationRuns", () => ({
  AutomationRuns: () => <div />,
}))
vi.mock("@/features/automations/components/TriggerMenu", () => ({
  TriggerMenu: ({
    onGitHub,
    onLinear,
  }: {
    onGitHub?: () => void
    onLinear?: () => void
  }) =>
    onGitHub ? (
      <>
        <button onClick={onGitHub}>Add GitHub trigger</button>
        <button onClick={onLinear}>Add Linear trigger</button>
      </>
    ) : null,
}))
vi.mock("@/features/agents/components/ModelPicker", () => ({
  ModelPicker: () => <div />,
}))
vi.mock("@/components/ui/switch", () => ({
  Switch: () => <input type="checkbox" />,
}))
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectItem: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectValue: () => <div />,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const TEMPLATE = {
  id: "nightly",
  name: "Nightly",
  description: "",
  prompt: "Check dependencies",
  schedule: "0 9 * * *",
  icon: (() => null) as never,
}

function signInAsAdmin() {
  vi.mocked(useSession).mockReturnValue({
    data: { is_admin: true },
  } as unknown as ReturnType<typeof useSession>)
}

describe("AutomationEditor", () => {
  it("shows the admin-thread checkbox only to admins", () => {
    vi.mocked(useSession).mockReturnValue({
      data: { is_admin: true },
    } as unknown as ReturnType<typeof useSession>)

    const adminMarkup = renderToStaticMarkup(<AutomationEditor mode="create" />)

    expect(adminMarkup).toContain("Run as admin thread")

    vi.mocked(useSession).mockReturnValue({
      data: { is_admin: false },
    } as unknown as ReturnType<typeof useSession>)

    const memberMarkup = renderToStaticMarkup(
      <AutomationEditor mode="create" />
    )

    expect(memberMarkup).not.toContain("Run as admin thread")
    expect(memberMarkup).toContain("This workspace automation is read-only")
    expect(memberMarkup).not.toContain("Save changes")
  })

  it("shows the workspace an existing automation runs in", () => {
    vi.mocked(useSession).mockReturnValue({
      data: { is_admin: true },
    } as unknown as ReturnType<typeof useSession>)

    const markup = renderToStaticMarkup(
      <AutomationEditor
        mode="edit"
        schedule={{
          id: "sched_1",
          name: "Nightly",
          prompt: "Check dependencies",
          schedule: "0 9 * * *",
          triggers: [{ id: "trigger_1", kind: "schedule", cron: "0 9 * * *" }],
          scope: "workspace",
          workspace: "core",
          adminThread: false,
          model: "Default",
          enabled: true,
        }}
      />
    )

    expect(markup).toContain(">Core<")
    expect(markup).not.toContain(">OSS<")
  })

  it("starts a new automation in the default workspace", () => {
    signInAsAdmin()
    render(<AutomationEditor mode="create" template={TEMPLATE} />)

    expect(
      screen.getByRole("button", { name: "Workspace" }).textContent
    ).toContain("Default")
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    expect(mocks.createMutate.mock.calls[0]?.[0]).toMatchObject({
      workspace: "default",
    })
  })

  it("sends a workspace the user picked", () => {
    signInAsAdmin()
    render(<AutomationEditor mode="create" template={TEMPLATE} />)

    fireEvent.click(screen.getByRole("button", { name: "Workspace" }))
    fireEvent.click(screen.getByRole("button", { name: /^Core/ }))
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    expect(mocks.createMutate.mock.calls[0]?.[0]).toMatchObject({
      workspace: "core",
    })
  })

  it("lets an automation whose workspace was deleted move to the only one left", () => {
    signInAsAdmin()
    const previous = mocks.workspaces
    mocks.workspaces = [previous[0]!]
    try {
      render(
        <AutomationEditor
          mode="edit"
          schedule={{
            id: "sched_1",
            name: "Nightly",
            prompt: "Check dependencies",
            schedule: "0 9 * * *",
            triggers: [
              { id: "trigger_1", kind: "schedule", cron: "0 9 * * *" },
            ],
            scope: "workspace",
            workspace: "gone",
            adminThread: false,
            model: "Default",
            enabled: true,
          }}
        />
      )

      expect(screen.getByText(/gone.*no longer exists/)).toBeTruthy()
      fireEvent.click(screen.getByRole("button", { name: "Workspace" }))
      expect(screen.getByRole("button", { name: /^Default/ })).toBeTruthy()
    } finally {
      mocks.workspaces = previous
    }
  })

  it("saves a GitHub trigger with its own repository beside the schedule", () => {
    signInAsAdmin()
    render(
      <AutomationEditor
        mode="edit"
        schedule={{
          id: "sched_1",
          name: "Nightly",
          prompt: "Check dependencies",
          schedule: "0 9 * * *",
          triggers: [{ id: "trigger_1", kind: "schedule", cron: "0 9 * * *" }],
          scope: "workspace",
          workspace: "default",
          adminThread: false,
          model: "Default",
          enabled: true,
        }}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Add GitHub trigger" }))
    fireEvent.click(screen.getByRole("button", { name: "Choose repository" }))
    fireEvent.click(screen.getByRole("button", { name: "PR merged" }))
    fireEvent.click(screen.getByRole("button", { name: "PR closed" }))
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))

    expect(mocks.updateMutate.mock.calls[0]?.[0].body.triggers).toEqual([
      { kind: "schedule", cron: "0 9 * * *" },
      {
        kind: "github",
        repo: "acme/oss",
        events: ["pull_request.closed", "pull_request.merged"],
      },
    ])
  })

  it("preserves and edits a workflow conclusion filter", () => {
    signInAsAdmin()
    render(
      <AutomationEditor
        mode="edit"
        schedule={{
          id: "sched_1",
          name: "Nightly",
          prompt: "Investigate failures",
          schedule: null,
          triggers: [
            {
              id: "trigger_1",
              kind: "github",
              repo: "acme/oss",
              events: ["workflow_run.completed", "issues.opened"],
              conclusion: "failure",
            },
          ],
          scope: "workspace",
          workspace: "default",
          adminThread: false,
          model: "Default",
          enabled: true,
        }}
      />
    )
    const filter = screen.getByLabelText(/Workflow conclusion/)
    expect((filter as HTMLSelectElement).value).toBe("failure")
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
    expect(mocks.updateMutate.mock.calls[0]?.[0].body.triggers).toEqual([
      {
        kind: "github",
        repo: "acme/oss",
        events: ["workflow_run.completed", "issues.opened"],
        conclusion: "failure",
      },
    ])
    fireEvent.change(filter, { target: { value: "" } })
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
    expect(mocks.updateMutate.mock.calls[1]?.[0].body.triggers).toEqual([
      {
        kind: "github",
        repo: "acme/oss",
        events: ["workflow_run.completed", "issues.opened"],
      },
    ])
  })

  it("saves a Linear trigger with its team and filters", () => {
    signInAsAdmin()
    render(<AutomationEditor mode="create" template={TEMPLATE} />)

    fireEvent.click(screen.getByRole("button", { name: "Add Linear trigger" }))
    fireEvent.change(screen.getByLabelText("Team key"), {
      target: { value: "eng" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Label added" }))
    fireEvent.change(screen.getByLabelText("Labels"), {
      target: { value: "agent-fix, p1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    expect(mocks.createMutate.mock.calls[0]?.[0].triggers).toEqual([
      { kind: "schedule", cron: "0 9 * * *" },
      {
        kind: "linear",
        team: "ENG",
        events: ["issue.labeled"],
        labels: ["agent-fix", "p1"],
        project: null,
        max_runs_per_hour: null,
      },
    ])
  })
})
