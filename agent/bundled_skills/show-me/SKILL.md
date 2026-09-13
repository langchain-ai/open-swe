---
name: show-me
description: Help the user understand the current topic visually — concise diagrams, code-shape sketches, real diffs and files via show_user, and focused HTML artifacts. Read this when explaining a design, a change, a flow, or a codebase area, or whenever you are about to paste code, a diff, or output into a message.
---

# Show, don't retype

Help the user understand the current topic visually. Skip the preamble and keep prose brief. Pick the smallest view that makes the key point clear, and place each visual next to the short text it supports.

Two kinds of visuals exist, and the split matters:

- **Sketches** are short, hand-shaped fences you write inline: pseudocode, call trees, component trees, file trees, Mermaid, and shape diffs. Use them for the *idea*.
- **Real content** — actual source, a real `git diff`, logs, generated output, screenshots — never goes into a message from memory. Write it to a file and call `show_user`. The user sees a highlighted, line-numbered card they can select lines in and comment on; you avoid transcription errors and wasted tokens.

## Sketches

Show logic or an algorithm as pseudocode:

```text
on(save)
  if content is unchanged
    return cached result
  write new content
  return fresh result
```

Show runtime control flow as a call tree:

```text
submitForm
  createSession
    persistPrompt
    launchAgent
  navigateToSession
```

Show UI structure as a component tree, including the state and module boundaries that matter:

```tsx
<SessionPage> (ui/src/routes/session.tsx)
  useSessionEvents()
  <SessionToolbar>
    <RunSkillButton> (packages/ui)
```

Show file responsibility or a broad refactor as a shallow file tree:

```text
agent/
├── tools/        # one module per tool, wired in server.py
├── middleware/   # ordered stack, see server.py
└── sandboxes/    # providers and reconnection
```

Show component interaction, control flow, or data flow with Mermaid. The dashboard renders ```` ```mermaid ```` fences inline; keep them to one idea and under ~15 nodes:

```mermaid
sequenceDiagram
    participant User
    participant UI
    participant Agent
    User->>UI: choose command
    UI->>Agent: send expanded prompt
    Agent-->>UI: stream result
```

Use a `diff` fence for a **shape** change when the surrounding shape already exists — a component tree, a file layout, a call tree, or pseudocode. Match the diff shape to the topic:

```diff
 submitForm
   createSession
     persistPrompt
+    expandSkillMention
     launchAgent
-  navigateToSession
+  navigateToSession
+    subscribeToEvents
```

Show a whole block inline only when it is short, mostly new, and the user needs a copyable target shape:

```ts
function expandSkill(command: string): string {
  return `use the ${command.slice(1)} skill`
}
```

## Real content: `show_user`

Reach for `show_user` whenever the visual is something a command can produce or a file already holds. It takes `command`, `path`, or both.

**Producing it: pass `command`.** The tool runs it, captures stdout to a kept file, and renders that file. Never run the command yourself and redirect its output by hand, and never retype the result.

- **Code you changed.** `show_user(command="git diff", title="Changes")`. Scope it when the full diff is noisy: `show_user(command="git diff main...HEAD -- src/")`.
- **Output and logs.** Test failures, build output, a generated config: `show_user(command="make test", title="Test run")`. A non-zero exit comes back to you as an error carrying stderr and stdout, so a broken command never renders a misleading card — read the error and fix the cause.
- **Choosing the renderer.** The output file's extension picks it, so add `path` for anything but code and diffs: `show_user(command="./scripts/gen-diagram", path=".open-swe/artifacts/flow.mmd")` renders a Mermaid diagram. `git diff` output is detected by content, so a diff needs no `path`.

**Already on disk: pass `path` alone.**

- **Code you are pointing at.** `show_user(path="agent/server.py", start_line=663, end_line=680, title="Download gating")`. The whole file renders with that range pre-selected, so the user keeps context and can widen the selection.
- **Screenshots and images.** `show_user(path=".open-swe/artifacts/after.png", title="Settings page after")`.
- **Documents.** A README, a design note, or any `.md` renders as formatted Markdown, including its Mermaid fences.

Generated output lands under `.open-swe/artifacts/`; add that path to the checkout's `.git/info/exclude`. Text is capped at 200 KB and images at 3 MB; narrow the command's output if you hit the cap.

After showing a card, reference it ("the highlighted lines in the card above") rather than restating its contents. The user can select lines in a text or diff card and press **Comment** to quote them into their reply, so a card is also how you invite precise feedback.

## Dense or interactive visuals

For a layout, a state comparison, an infographic, or a concept too dense for Mermaid, write one focused `.html` file and publish it with `show_user` (hosted dashboard runs), `save_plan` (anywhere, including desktop), or `slack_attach_html` (Slack). `show_user` serves the preview from a signed sandbox URL, so it needs a hosted LangSmith sandbox and returns an error on desktop or another provider. Read the `html-artifacts` skill first for the authoring rules. Match the product's colors, type, and components; use real labels and data.

## Surface

- **Dashboard / desktop:** everything above applies. `show_user` and inline Mermaid are available.
- **Slack:** `show_user` is unavailable and Mermaid does not render. Keep sketches to `text` fences, and put anything longer — diffs, diagrams, multi-section explanations — in a `save_plan` artifact and send only its link.

You may use one of these views, you may use several; you will rarely use all of them. Don't overwhelm the user.

---

Adapted from the `show-me` skill in [humanlayer/skills](https://github.com/humanlayer/skills) (MIT).
