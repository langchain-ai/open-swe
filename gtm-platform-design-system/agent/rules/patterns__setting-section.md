# SettingSection

A row is one setting: a label, at most a one line description beneath it, and exactly ONE control opposite. Two controls in a lane are two settings that have not been split yet, and a description that needs a second line is a section, no...

Import: `@langchain/gtm-platform-design-system/patterns/setting-section`
Source: `src/patterns/setting-section.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### SettingRow

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `description` | `string` | no |
| `badge` | `ReactNode` | no |
| `density` | `"default" \| "compact"` | no |
| `onSave` | `(value: SettingValue) => Promise<void>` | no |
| `control` | `(slot: SettingControlSlot) => ReactNode` | yes |

### SettingSection

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `description` | `string` | no |
| `actions` | `ReactNode` | no |
| `contained` | `boolean` | no |
| `id` | `string` | no |
| `children` | `ReactNode` | yes |

## The decisions this carries

- A row is one setting: a label, at most a one line description beneath it, and exactly ONE control opposite. Two controls in a lane are two settings that have not been split yet, and a description that needs a second line is a section, not a row.
- SAVE SEMANTICS, ADOPTED FROM THE ROTHENBERG MODEL, PENDING AMAL'S CONFIRMATION: settings save on change, per row. There is no Save button, no Cancel button, and no page level dirty bar on a settings surface, because a settings page is a set of independent switches rather than one document with a commit point. The row that changed is the row that saves, and the row that failed is the only row that has to be retried.
- Because there is no Save button, the row owes a transient acknowledgement: Saving while the promise is pending, Saved for a moment after it resolves, and the reason inline on the risk role when it rejects. The status lane is reserved at all times, so an acknowledgement never reflows the page, and it never animates. Save on change fires on every toggle, and a high frequency interaction gets no motion.
- A rejected save leaves the row exactly as the user set it, states the reason under the control, and marks the control aria-invalid pointing at that reason. It never silently reverts, and it never becomes a toast: the failure belongs beside the setting that failed.
- The control sits in a fixed lane, not flexed to the right edge. A page of rows then has one control edge, which is what makes a settings surface read as composed rather than assembled. The lane is a constant here and wants a named step in the geometry ladder; it is a stock width until tokens.css reopens.
- The row stacks when ITS CONTAINER is narrow, through a container query, never a viewport breakpoint. A settings page under a 420px agent dock is narrow in its container while the viewport is wide, and a breakpoint gets that case wrong every time.
- The row owns its separator and cancels it on the last child in CSS. The parent stays a plain map with no index arithmetic and no `index > 0 && <Separator/>` at the call site, which is how a stray rule above the first row happens.
- Badges ride inline with the label, never in the control lane. Required, Beta, Recommended are all statements about the setting, and putting them in the lane breaks the one control edge the lane exists to create.
- Identity first, danger last. The rows a user came to read about themselves lead the page; anything destructive leaves this file entirely and arrives through PageFrame's `dangerZone` slot, which pins it to the bottom structurally. A destructive row is not a row variant.
- A destructive control inside a row still composes ConfirmableAction. Save on change means one click commits, and one click must never be able to commit something irreversible.
- No collapsible sections inside the reading column. Product Settings may swap the app rail for a scoped section jump list; that is chrome, not a second page organisation device inside PageFrame. Collapse still hides the thing the user came to change and is rejected.
- Product settings are separate pages under `/settings/*` with compact feature rows per flag (see SETTINGS_PRODUCT_RULES). SettingSection remains the dense mixed-control organism for `/design`. Do not force `headerPlacement="in-panel"` on it.
- A boolean setting takes Switch from `components/ui/switch`. A switch states that something is on right now and flips it immediately, which is exactly what save on change does; a checkbox states an intention that a Save button will later act on, and there is no Save button here. Checkbox stays correct for the other question, which is membership: a row that means 'which of these' rather than 'is this on' takes checkboxes. Do not hand roll either at a call site.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
