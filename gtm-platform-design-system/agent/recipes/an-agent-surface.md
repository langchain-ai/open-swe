# An agent surface

A transcript, the list of conversations beside it, and whatever the work needs
next to both. This is the shape of a coding agent's main screen.

## Order of decisions

**1. Pick the shell mode before anything else.** `AppShell` has three, and they
are not interchangeable. `flat` mounts an optional header. `lineage` mounts one
`PageBand`. `focus` swaps the navigation rail for the thread rail. An agent
surface is almost always `focus`.

```tsx
import { AppShell } from "@langchain/gtm-platform-design-system/patterns/app-shell";
import { AgentThreadRail } from "@langchain/gtm-platform-design-system/patterns/agent-thread-rail";
import { AgentThread } from "@langchain/gtm-platform-design-system/patterns/agent-thread";
```

**2. The rail replaces navigation; it is not a third sidebar.** `AgentThreadRail`
owns grouping, pinning, unread state and running state. Read its rules: status
shows on the row and never as a group, a blue dot means unread and never
completed, and read and unread titles share one weight.

**3. The thread has a measure, and it is not a page measure.** 704px, centred in
the work pane — a documented exception to the four width rungs, held in one
token. Do not re-derive it.

**4. Progress and durable work are different things, and the difference is
load-bearing.** Tool activity is transient: it appears, it resolves, it stops
mattering. A decision, a review set, a receipt or a blocking input is durable and
gets a typed surface. Never branch on a raw tool name to decide how to render
something — that is the coupling this separation exists to prevent.

**5. One bounded decision in the transcript.** A decision inline in the thread
owns its own edit state and nothing else. Collections and multi-step work belong
to a route. A right-hand work pane exists only for one selected member of a
mounted list.

**6. The agent never chooses layout.** It can reference a route or a resource;
the host resolves the destination and renders an explicit action. A model that
can mint a URL or move a pane is a model that can move your UI.

## Composing the panes

`SplitView` names panes by role, not by side: one is the work pane where the
decision happens, the other is reference (facts the decision is made against) or
list (the thing being traversed). The work pane never remounts when posture
changes — losing scroll position mid-review is the failure this rule is about.

## What is not here yet

`AgentCompanionChrome`, `AgentDockSlot` and `AgentLayoutMenu` stayed in the
application: they read session state directly and need it injected as props
first. If you need the dock or the layout menu, ask — it is a known piece of
work.
