/** @vitest-environment jsdom */

import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AutomationEditor } from "./AutomationEditor"
import { useSession } from "@/lib/session"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useCreateAgentSchedule: () => ({
    error: null,
    isPending: false,
    mutate: vi.fn(),
  }),
  useDeleteAgentSchedule: () => ({
    error: null,
    isPending: false,
    mutate: vi.fn(),
  }),
  useUpdateAgentSchedule: () => ({
    error: null,
    isPending: false,
    mutate: vi.fn(),
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
vi.mock("@/features/settings/components/RepoSelector", () => ({
  RepoSelector: () => <div />,
}))
vi.mock("@/features/automations/components/AutomationRuns", () => ({
  AutomationRuns: () => <div />,
}))
vi.mock("@/features/automations/components/ScheduleTriggerPicker", () => ({
  ScheduleTriggerPicker: () => <div />,
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
  vi.clearAllMocks()
})

describe("AutomationEditor", () => {
  it("shows both automation thread modes", () => {
    vi.mocked(useSession).mockReturnValue({
      data: { is_admin: false },
    } as unknown as ReturnType<typeof useSession>)

    const markup = renderToStaticMarkup(<AutomationEditor mode="create" />)

    expect(markup).toContain("Start a new thread")
    expect(markup).toContain("Reuse one thread")
  })

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
  })
})
