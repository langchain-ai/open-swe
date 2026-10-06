# RecordHeader

RecordHeader is object identity inside the work pane — title, status, and quiet meta. It is not a PageBand and must not host a second band of filters.

Import: `@langchain/gtm-platform-design-system/patterns/record-header`
Source: `src/patterns/record-header.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### RecordInlineEdit

| Prop | Type | Required |
|---|---|---|
| `error` | `string` | no |
| `kind` | `RecordInlineEditKind` | yes |
| `label` | `string` | yes |
| `onCancel` | `() => void` | yes |
| `onChange` | `(value: string) => void` | yes |
| `onCommit` | `() => void` | yes |
| `placeholder` | `string` | no |
| `value` | `string` | yes |

### RecordHeader

| Prop | Type | Required |
|---|---|---|
| `title` | `ReactNode` | yes |
| `subtitle` | `string` | no |
| `status` | `ReactNode` | no |
| `meta` | `ReactNode` | no |
| `actions` | `ReactNode` | no |
| `mark` | `ReactNode` | no |
| `className` | `string` | no |

### RecordFact

| Prop | Type | Required |
|---|---|---|
| `icon` | `Glyph` | yes |
| `label` | `string` | yes |
| `value` | `ReactNode` | yes |
| `copyable` | `boolean` | no |
| `expandable` | `boolean` | no |
| `edit` | `Omit<RecordInlineEditProps, "kind">` | no |

### RecordHop

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `onClick` | `() => void` | yes |

## The decisions this carries

- RecordHeader is object identity inside the work pane — title, status, and quiet meta. It is not a PageBand and must not host a second band of filters.
- The ghost back hop is RecordHop: `h-control`, outside `PAGE_STACK_CLASS`. Shell `pt-2` plus this row is the same line as AppRailBrand. Never put the hop inside `py-6`.
- The title sits on a control-height row with actions trailing, the same identity band Inbox and Alerts use. Type order is title → optional short subtitle → facts. Three rungs: `text-title` (name), `text-label` (subtitle), fact rows (status, type, window, description).
- The identity stack uses `gap="md"` under the title and `gap="sm"` between support lines so the name and facts do not kiss. The hairline sits after `gap="lg"`.
- A long object description is a RecordFact (`expandable`), not a free line under the title. Collapsed copy uses `line-clamp-3`; click the copy to expand the rest in the value lane, click again to clamp. Do not add a Show more control, and do not clip the expanded copy in a scrollport. Icon and label sit in `h-control` on every fact row so Description padding matches Status / Type / Window. A one-line Description sits in `min-h-control` and centers with that icon and label. Expanded copy stays top-aligned. A short commercial subtitle (Accounts) may still use the subtitle slot and truncates on `max-w-xl`.
- Status may sit on the title or in a RecordFact row as a Quiet badge. Do not draw it twice. Reporting and table filters do not belong here.
- Object facts use RecordFact: icon, fixed label lane, value. That is the Inbox / Alerts property row, not a chip strip. Primary actions sit trailing on the title row. Keep one solid primary; everything else Quiet or outline. A mailbox or identifier may set `copyable`: hover and focus reveal a ghost Copy in the value lane. The button name stays Copy {label}; a live region says Copied. Do not copy every fact.
- When identity is writable, the title is the field. Description stays an expandable RecordFact: collapsed copy uses `line-clamp-3`, click expands the same copy in the value lane. A manager's editor mounts only after that expand and grows with the copy; it is not a two-line scroll field. Campaign type reuses the create ChoiceCards (icon, label, description) from an outline Button on the control rung. Window uses the stock DatePicker on that same height. Hover and focus reveal the title field; picker commit saves type and window. The title field drops Input's default horizontal pad so the name lines up with the fact rows. Single-line values, including a one-line Description, sit in `min-h-control` and center with the icon and label. Expanded Description stays top-aligned. Do not add a sibling Edit dialog or a trailing settings section for those same fields.
- Compose StatReadout beside the header when the board needs a provisional figure — never invent KPI tiles in chrome.
- Shared by Accounts detail and Campaign Studio. Prefer this over page-local identity stacks.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
