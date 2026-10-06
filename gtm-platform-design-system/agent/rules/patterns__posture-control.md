# PostureControl

The sanctioned agent toggle is this segmented Tabs control in the sidebar. A floating duplicate in a global header is forbidden (`APP_SHELL_RULES`).

Import: `@langchain/gtm-platform-design-system/patterns/posture-control`
Source: `src/patterns/posture-control.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### PostureControl

| Prop | Type | Required |
|---|---|---|
| `value` | `Posture` | yes |
| `onChange` | `(next: Posture) => void` | yes |
| `agentAttention` | `boolean` | no |
| `collapsed` | `boolean` | no |
| `className` | `string` | no |

## The decisions this carries

- The sanctioned agent toggle is this segmented Tabs control in the sidebar. A floating duplicate in a global header is forbidden (`APP_SHELL_RULES`).
- Two segments only: Home and Agent, each with its Icon (Home / Bot). Adding Slack or Dock as a third segment invents a posture the shell does not mount.
- Compose `Tabs` + `TabsList` + `TabsTrigger` — never a hand-rolled segmented track. Geometry and selected treatment already live on the primitive.
- This control does not own chrome pad. The rail that mounts it — AgentThreadRail `leading`, or a single `padding="sm"` stack above SidebarNav — owns the inset. Never wrap it in a second `px-2` / `py-2`.
- An unread cue on Agent is a 6px primary dot, never a count badge. Counts belong on nav rows.
- ⌘J toggles Agent. The control and the shortcut are one decision.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
