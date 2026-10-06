# PageFrame

A page is one readable column, 768px wide, centred in whatever the shell gives it. That is `max-w-3xl` off the stock scale, chosen because it is the widest step where a label and description on the left and their control on the right sti...

Import: `@langchain/gtm-platform-design-system/patterns/page-frame`
Source: `src/patterns/page-frame.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### PageSection

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `icon` | `Glyph` | no |
| `description` | `string` | no |
| `actions` | `ReactNode` | no |
| `contained` | `boolean` | no |
| `inset` | `PageSectionInset` | no |
| `flush` | `boolean` | no |
| `headerPlacement` | `PageSectionHeaderPlacement` | no |
| `id` | `string` | no |
| `children` | `ReactNode` | yes |

### PageFrame

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `description` | `string` | no |
| `hideTitle` | `boolean` | no |
| `actions` | `ReactNode` | no |
| `children` | `ReactNode` | yes |
| `dangerZone` | `ReactNode` | no |
| `dangerTitle` | `string` | no |
| `dangerDescription` | `string` | no |

## The decisions this carries

- A page is one readable column, 768px wide, centred in whatever the shell gives it. That is `max-w-3xl` off the stock scale, chosen because it is the widest step where a label and description on the left and their control on the right still read as one row rather than two columns the eye has to travel between. Wider is a table surface, which is a different family and gets its own frame when that decision is made.
- This column is the width law's reading measure. 768 = `max-w-3xl` = `--gtm-container-reading` = AppShell's `contentWidth='reading'`: one number reached three ways, deliberately, so a PageFrame route inherits the cap once rather than fighting a second one. The number is unchanged by the width law (Primer's `medium` 768, Tailwind's `max-w-3xl`); what the law added is the other three measures, which live above this frame. (`APP_SHELL_RULES`; `docs/plan/gtm-agent-product/width-padding-evidence.md` section 5.3 rule 3.)
- Width above the frame belongs to AppShell, and the frame takes no width prop. A route that needs a different measure asks the shell for one of the four named values (reading / work / wide / interstitial); a surface wider than this column is a different page family, not a wider PageFrame. The shell contributes the route gutter (`px-4 lg:px-6`). PageFrame adds vertical rhythm only (`PAGE_STACK_CLASS` / `py-6`) — never a second full `padding="xl"` / `p-6` inside the measure.
- The header is the title on the page step plus, at most, a one line description on ink-subtle. A description that needs a second line is a section, not a header.
- That header is welded to this column and cannot be borrowed. A work or wide route that needs a page title takes `PageMasthead`, which is the same strip without the 768 cap and with the counts lane; it never lifts these classes into an archetype file. Two components draw a page title, deliberately, and the width is what separates them: inside the reading column it is the frame's, outside it is the masthead's.
- Sections render in source order with one gap between them, top to bottom. No tabs, no accordions, no reordering by importance: the order the ticket lists them in is the order the page shows them in.
- If the page has a destructive or irreversible section it is last, always, and it arrives through the `dangerZone` slot rather than as another child. The frame renders it after everything else behind a line-strong seam with a risk toned heading, so putting it third from the top is not a thing this pattern can express.
- Whatever sits in the danger zone still owes its own confirmation: pair it with ConfirmableAction. The zone is where a dangerous control lives, not permission for it to fire on one click.
- Tabs are not a page organisation device in this system yet. Tabs versus stacked sections is on the open decisions list in wiki 02 ('Decisions, not components: the patterns tier'), which means a surface that wants them files the decision rather than shipping them.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
