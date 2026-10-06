# Catalog

The GTM Platform internal design system, created by Amal Irgashev.

73 primitives, 41 patterns, 43 carrying explicit rules. Generated; do not edit.

A primitive is a part. A pattern is a decision already made, and the rules
file is where that decision is stated. Reach for a pattern first.

`agent/index.json` is the same data with search terms and export lists, which
is the one to grep when you know the job but not the name.

## Primitives

| Component | Import | Rules | What it is |
|---|---|---|---|
| `AiChatInput` | `ui/ai-chat-input` | — | PromptInput — gallery / reference composer with spring expand-collapse. |
| `Alert` | `ui/alert` | — | Alert, on CORE 14. API reference: the ReUI free `alert` (reui.io/r/alert.json, fetched keyless 2026-08-05) -- its part names, its `data-slot` anatomy  |
| `Avatar` | `ui/avatar` | — | Avatar, on CORE 14. |
| `Badge` | `ui/badge` | — | Badge, retokenized onto CORE 14. |
| `Box` | `ui/box` | — | Typed layout primitives (the Orbit escalation, wiki 02). |
| `Button` | `ui/button` | — | Button, retokenized onto CORE 14. |
| `Calendar` | `ui/calendar` | — | Calendar, on CORE 14. |
| `CheckboxGroup` | `ui/checkbox-group` | — | Checkbox group, on CORE 14. The fluid-functionalism item (ANALYSIS.md row 21) adapted onto Base UI's `CheckboxGroup` -- the Base flavour, not the Radi |
| `Checkbox` | `ui/checkbox` | — | Checkbox, on CORE 14. |
| `Cn` | `ui/cn` | — | Design-system-aware class merge. |
| `Collapsible` | `ui/collapsible` | — | Collapsible, on CORE 14. |
| `Combobox` | `ui/combobox` | — | Combobox, on CORE 14: the picker. |
| `Command` | `ui/command` | — | Command, on CORE 14. |
| `ContextMenu` | `ui/context-menu` | — |  |
| `ConversationNavigator` | `ui/conversation-navigator` | — |  |
| `DatePicker` | `ui/date-picker` | — | Date picker, on CORE 14. |
| `Dialog` | `ui/dialog` | — | Dialog, retokenized onto CORE 14. |
| `DropdownMenu` | `ui/dropdown-menu` | — | Dropdown menu, retokenized onto CORE 14. |
| `Elevated` | `ui/elevated` | [16](agent/rules/ui__elevated.md) | Depth is computed, never picked: declare an offset, let the context resolve the level. |
| `Empty` | `ui/empty` | — | Empty — compositional zero-data chrome (shadcn / ReUI Empty family, CORE 14). |
| `FadeText` | `ui/fade-text` | [3](agent/rules/ui__fade-text.md) | FadeText says there is more. It never uses an ellipsis. The cut is a trailing mask, not a painted wash, so hover and selected fills stay honest. |
| `FieldFocus` | `ui/field-focus` | — | Shared focus and invalid treatment for bordered form fields. |
| `Frame` | `ui/frame` | — | Frame, retokenized onto CORE 14. |
| `Glyphs` | `ui/glyphs` | — | The glyph barrel. |
| `GroupRow` | `ui/group-row` | [4](agent/rules/ui__group-row.md) | A group row is a row in the same grid, not a heading outside it. The anatomy is fixed: disclosure chevron, then the group identity, then the compact c |
| `HoverCard` | `ui/hover-card` | — | Hover card, on CORE 14. Base UI calls it a preview card; the product calls it a hover card, and the product name wins at the boundary. |
| `IconWell` | `ui/icon-well` | — | IconWell — the 24px container every channel mark and tool step icon sits in. |
| `Icon` | `ui/icon` | — | The one Icon wrapper (wiki 02: "one set, one wrapper"). |
| `InputCopy` | `ui/input-copy` | — | Input copy, on CORE 14. The fluid-functionalism item (ANALYSIS.md row 29) rebuilt from our own parts: a read-only field carrying a value nobody types  |
| `Input` | `ui/input` | — | Input, retokenized onto CORE 14. |
| `Kbd` | `ui/kbd` | — | Kbd, on CORE 14. The keyboard hint chip. |
| `Label` | `ui/label` | — | Label, on CORE 14. |
| `MarkdownCodec` | `ui/markdown-codec` | — |  |
| `MarkdownEditor` | `ui/markdown-editor` | — | Rich Markdown editor. The surface is TipTap so play prompts edit like formatted text; onChange still emits the Markdown string the agent executes. Col |
| `MentionChip` | `ui/mention-chip` | — | MentionChip — Slack-shaped entity pill. |
| `OrbGlyph` | `ui/orb-glyph` | — | The static Orb glyph: the agent's mark when a button or label needs a drawing rather than the animated loading canvas in `ui/orb.tsx`. |
| `Orb` | `ui/orb` | [8](agent/rules/ui__orb.md) | A call site names what the agent is DOING, never what the orb looks like. `activity` is the product's verb set; the library's nine animation names are |
| `Popover` | `ui/popover` | — | Popover, on CORE 14. |
| `PopupSurface` | `ui/popup-surface` | — | Shared surface for every anchored float in the product. |
| `Progress` | `ui/progress` | — | Progress, on CORE 14. |
| `ProviderLogos` | `ui/provider-logos` | — | Provider brand marks (Simple Icons / brand-kit paths). |
| `PushpinGlyph` | `ui/pushpin-glyph` | — |  |
| `RadioGroup` | `ui/radio-group` | — | Radio group, on CORE 14. |
| `ScrollArea` | `ui/scroll-area` | — | Scroll area, on CORE 14. This file carries THE SCROLLBAR LAW (wiki 02): "the thumb appears on hover or while scrolling and fades after; the track is i |
| `ScrollFadeContainer` | `ui/scroll-fade-container` | — | Scroll fade container, retokenized onto CORE 14. |
| `SearchInput` | `ui/search-input` | — | Search input, on CORE 14. The one filter field. |
| `Select` | `ui/select` | — | Select, on CORE 14. |
| `Separator` | `ui/separator` | — | Separator, retokenized onto CORE 14. |
| `Sheet` | `ui/sheet` | — | Sheet — floating edge panel on CORE 14. |
| `Shortcut` | `ui/shortcut` | [3](agent/rules/ui__shortcut.md) | Kbd is one key. Shortcut is the chord: a row of Kbd chips, never a string like ⌘K inside one chip. |
| `Sidebar` | `ui/sidebar` | — | Sidebar, retokenized onto CORE 14. |
| `Skeleton` | `ui/skeleton` | — | Skeleton, retokenized onto CORE 14. |
| `Slider` | `ui/slider` | — | Slider, on CORE 14. The fluid-functionalism item (ANALYSIS.md row 40) re-expressed on Base UI, because we shipped no slider at all and the consumers a |
| `Sonner` | `ui/sonner` | — | Toaster, retokenized onto CORE 14. |
| `SpinnerGlyph` | `ui/spinner-glyph` | — | The Spinner glyph: the one indeterminate loading mark. |
| `Spinner` | `ui/spinner` | — | Spinner, on CORE 14. The one loading glyph in the product. |
| `Switch` | `ui/switch` | — | Switch, on CORE 14. |
| `Table` | `ui/table` | — | Table, on CORE 14. |
| `Tabs` | `ui/tabs` | — | Tabs, retokenized onto CORE 14. |
| `Textarea` | `ui/textarea` | — | Textarea, on CORE 14. |
| `Timeline` | `ui/timeline` | — | Timeline, retokenized from ReUI's Base UI timeline (https://reui.io/docs/components/base/timeline, registry `timeline`). |
| `ToggleGroup` | `ui/toggle-group` | — | Toggle group, on CORE 14. This is the segmented control base. |
| `Tooltip` | `ui/tooltip` | — | Tooltip, retokenized onto CORE 14. |

## Patterns (decisions)

| Component | Import | Rules | What it is |
|---|---|---|---|
| `AgentThreadRail` | `patterns/agent-thread-rail` | [15](agent/rules/patterns__agent-thread-rail.md) | In focus mode this rail replaces the navigation rail — it is not a third sidebar. Width follows AppShell's rail (246–400px, default 246). |
| `AgentThread` | `patterns/agent-thread` | [7](agent/rules/patterns__agent-thread.md) | The thread measure is 704px — a documented contentWidth exception, not a fifth rung (`APP_SHELL_RULES`). Centre it in the work pane. The number lives  |
| `AppRailPreview` | `patterns/app-rail-preview` | [4](agent/rules/patterns__app-rail-preview.md) | Collapsed destination icons open one contextual HoverCard to the right after hover or focus intent. Expanded rows already carry their labels and do no |
| `AppShell` | `patterns/app-shell` | [23](agent/rules/patterns__app-shell.md) | The header is earned, not permanent. The sidebar already says where you are, so chrome above the content has to justify itself on every route rather t |
| `CapacityMetric` | `patterns/capacity-metric` | [6](agent/rules/patterns__capacity-metric.md) | Capacity is a remaining allowance, not usage completed. The dominant number says what is left and the meter fills in the same direction. |
| `ChangeFeed` | `patterns/change-feed` | [8](agent/rules/patterns__change-feed.md) | ChangeFeed is the quieter Home band under the DecisionRow queue. It is observational — no Approve CTA, no action lane. Home is a router, so a row that |
| `ChoiceCards` | `patterns/choice-cards` | [4](agent/rules/patterns__choice-cards.md) | A fork is cards, not a radio list and not two forms stacked. Each card names the path and what it opens. |
| `ComingUpCalendar` | `patterns/coming-up-calendar` | [5](agent/rules/patterns__coming-up-calendar.md) | List is the default Coming up view. List / Calendar is a compact icon switch in the section header, replacing the meeting count. 3 days, week, and mon |
| `ConfirmableAction` | `patterns/confirmable-action` | [10](agent/rules/patterns__confirmable-action.md) | Required, not optional, for any action that permanently destroys something or sends on a rep's behalf: deleting a campaign, wiping account memory, rel |
| `ConversationRow` | `patterns/conversation-row` | [6](agent/rules/patterns__conversation-row.md) | An inbox row is not a QueueRow. It is a conversation rung with a 32px mark lane, contact over account, and a trailing column of meta over the status c |
| `DataScopeSelect` | `patterns/data-scope-select` | — |  |
| `DecisionRow` | `patterns/decision-row` | [6](agent/rules/patterns__decision-row.md) | Home is a router. The row's only verb is Open — never Approve, Send, or Snooze on the Home queue (platform decision 1.2). Owning surfaces hold the dec |
| `DiffRow` | `patterns/diff-row` | [5](agent/rules/patterns__diff-row.md) | DiffRow shows one field mutation: caps label · before → after. The default is chips (muted line-through before, ink after). `layout="block"` is the sa |
| `EmptyState` | `patterns/empty-state` | [6](agent/rules/patterns__empty-state.md) | Compose `ui/empty` (Header → Media → Title → Description, optional Content). Never a bare centred `<p>` pretending to be an empty surface. Reference:  |
| `FilterableTable` | `patterns/filterable-table` | [25](agent/rules/patterns__filterable-table.md) | A catalog may offer Table and Gallery as two layouts of this same dataset. The surface supplies card content and URL-backed view state; this pattern k |
| `FormField` | `patterns/form-field` | [12](agent/rules/patterns__form-field.md) | This is the explicit submit family: creation flows, invite flows, wizard steps. Nothing has happened until the user presses the button. If the surface |
| `InputRequest` | `patterns/input-request` | [14](agent/rules/patterns__input-request.md) | Use InputRequest only to collect missing information. ApprovalArtifact remains the boundary for a consequential send or write. |
| `MetricCard` | `patterns/metric-card` | [12](agent/rules/patterns__metric-card.md) | Dashboard headline metrics are cards with an icon, a clear title, one dominant value, and one context line. Dense record metadata remains a StatReadou |
| `OnboardingDialog` | `patterns/onboarding-dialog` | [10](agent/rules/patterns__onboarding-dialog.md) | OnboardingDialog teaches a surface. WizardDialog remains the create flow. Do not put fields in this dialog. |
| `OverflowTabs` | `patterns/overflow-tabs` | [9](agent/rules/patterns__overflow-tabs.md) | Fit tabs that fit; park the rest under Other + Popover. Never a horizontal ScrollArea on a tab strip — especially not inside a narrow list sidebar. |
| `PageBand` | `patterns/page-band` | [8](agent/rules/patterns__page-band.md) | Chrome may only assert what the content cannot: where you are (the sidebar), how deep you are (a lineage band), what you can do (the page toolbar). Ze |
| `PageFrame` | `patterns/page-frame` | [9](agent/rules/patterns__page-frame.md) | A page is one readable column, 768px wide, centred in whatever the shell gives it. That is `max-w-3xl` off the stock scale, chosen because it is the w |
| `PageMasthead` | `patterns/page-masthead` | [10](agent/rules/patterns__page-masthead.md) | A masthead is content, not chrome. It states what today is and what is in it; PageBand states where you are and what you can do. That is why Home moun |
| `PostureControl` | `patterns/posture-control` | [6](agent/rules/patterns__posture-control.md) | The sanctioned agent toggle is this segmented Tabs control in the sidebar. A floating duplicate in a global header is forbidden (`APP_SHELL_RULES`). |
| `ProviderAction` | `patterns/provider-action` | [3](agent/rules/patterns__provider-action.md) | A provider hop is a compact outline control with the shipped ProviderLogo and a verb. Do not draw a second local Salesforce or LinkedIn button. |
| `ProviderMark` | `patterns/provider-mark` | [4](agent/rules/patterns__provider-mark.md) | Provider identity is a real brand mark in an IconWell, not an initial tile and not a bare SVG. Inbox rows, tool activity, receipts, and thread-rail Ho |
| `QueueRow` | `patterns/queue-row` | [11](agent/rules/patterns__queue-row.md) | Lanes are fixed-width so the columns line up down the queue: 16px checkbox, 10px state dot, the flexible identity, 110px badge, 100px meta, 28px actio |
| `Receipt` | `patterns/receipt` | [7](agent/rules/patterns__receipt.md) | Receipt is the terminal step of CopyGrammar: Object → State → Evidence → Action → Receipt. Render inline under the action and keep it inspectable. |
| `RecordHeader` | `patterns/record-header` | [10](agent/rules/patterns__record-header.md) | RecordHeader is object identity inside the work pane — title, status, and quiet meta. It is not a PageBand and must not host a second band of filters. |
| `ReviewBand` | `patterns/review-band` | [6](agent/rules/patterns__review-band.md) | A review band presents one object to act on. It is not a list row, not a New-this-week card, and not a contained PageSection. Suggested play is a titl |
| `SchemaCell` | `patterns/schema-cell` | [8](agent/rules/patterns__schema-cell.md) | A cell has a schema type, and the type picks the chrome. Surfaces do not restyle the same field two ways. |
| `SettingSection` | `patterns/setting-section` | [13](agent/rules/patterns__setting-section.md) | A row is one setting: a label, at most a one line description beneath it, and exactly ONE control opposite. Two controls in a lane are two settings th |
| `SidebarNav` | `patterns/sidebar-nav` | [11](agent/rules/patterns__sidebar-nav.md) | Selection is a fill, ink and weight. Never a border, never a shadow, never a left bar. A selected row is the same height in the same place with `bg-se |
| `SidebarTree` | `patterns/sidebar-tree` | — | Rules for sidebar trees, shared by the Agent rail and memory browser. Agent folder headings use their identity icon; sections use a trailing chevron.  |
| `SiteMark` | `patterns/site-mark` | — | - A link to an external site wears that site's favicon at the data rung, and nothing else. No provider wordmark, no coloured chip, no letter avatar. - |
| `SplitView` | `patterns/split-view` | [20](agent/rules/patterns__split-view.md) | Panes have roles, not sides. One is the work pane, where the decision happens; the other is either the reference pane (facts the decision is made agai |
| `StatReadout` | `patterns/stat-readout` | [12](agent/rules/patterns__stat-readout.md) | StatReadout is the dense number pattern for record chrome, metadata columns, and compact run summaries. Six settled boards put those values in a label |
| `StateNotice` | `patterns/state-notice` | [9](agent/rules/patterns__state-notice.md) | This is the one shape for a thing that did not happen: a send the door refused, a job that stopped, a connection that lapsed, an import that found not |
| `TableFilters` | `patterns/table-filters` | [7](agent/rules/patterns__table-filters.md) | This popover writes request state. It never filters mounted rows. The table renders the page the server returned for the current values. |
| `VersionedEditor` | `patterns/versioned-editor` | [12](agent/rules/patterns__versioned-editor.md) | VersionedEditor is the third save family. Prompt changes stay browser-local until Save draft updates the one mutable team draft or Publish creates the |
| `WizardDialog` | `patterns/wizard-dialog` | [11](agent/rules/patterns__wizard-dialog.md) | Use one Dialog, one scroll body, and one footer. A branch changes the current step; it never opens a second dialog. |

## Data grid

| Component | Import | Rules | What it is |
|---|---|---|---|
| `DataGridColumnFilter` | `data-grid/data-grid-column-filter` | — | ReUI `data-grid` column filter, vendored (free tier, no licence key) and retokenized. Source: https://reui.io/r/data-grid.json, 2026-08-05. |
| `DataGridColumnHeader` | `data-grid/data-grid-column-header` | — | ReUI `data-grid` column header, vendored (free tier) and retokenized. Source: https://reui.io/r/data-grid.json, 2026-08-04. |
| `DataGridColumnVisibility` | `data-grid/data-grid-column-visibility` | — | ReUI `data-grid` column visibility menu, vendored (free tier) and retokenized. Source: https://reui.io/r/data-grid.json, 2026-08-04. |
| `DataGridExpandedRows` | `data-grid/data-grid-expanded-rows` | — | Expand-as-rows paint for FilterableTable. Not vendored: the ReUI grid only ships a colspan detail slot. Child rows reuse parent leaf columns for pin a |
| `DataGridGlyphs` | `data-grid/data-grid-glyphs` | — | The grid's glyph set. |
| `DataGridLoadMore` | `data-grid/data-grid-load-more` | — |  |
| `DataGridPagination` | `data-grid/data-grid-pagination` | — | ReUI `data-grid` pagination, vendored (free tier) and retokenized. Source: https://reui.io/r/data-grid.json, 2026-08-04. |
| `DataGridTableVirtual` | `data-grid/data-grid-table-virtual` | — | ReUI `data-grid` virtual table, vendored (free tier) and retokenized. Source: https://reui.io/r/data-grid.json, 2026-08-04. See data-grid-table.tsx fo |
| `DataGridTable` | `data-grid/data-grid-table` | — | ReUI `data-grid` table, vendored (free tier, no licence key) and retokenized. |
| `DataGrid` | `data-grid/data-grid` | — | ReUI `data-grid` core, vendored (free tier, no licence key) and retokenized. |
