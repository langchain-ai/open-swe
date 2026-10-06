# ComingUpCalendar

List is the default Coming up view. List / Calendar is a compact icon switch in the section header, replacing the meeting count. 3 days, week, and month are the same compact icon switch, calendar-only. There is no drag, create, recurrenc...

Import: `@langchain/gtm-platform-design-system/patterns/coming-up-calendar`
Source: `src/patterns/coming-up-calendar.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ComingUpCalendar

| Prop | Type | Required |
|---|---|---|
| `meetings` | `readonly ComingUpMeeting[]` | yes |
| `now` | `number` | yes |
| `onOpen` | `(meeting: ComingUpMeeting) => void` | yes |
| `mode` | `ComingUpMode` | no |
| `defaultRange` | `ComingUpRange` | no |

## The decisions this carries

- List is the default Coming up view. List / Calendar is a compact icon switch in the section header, replacing the meeting count. 3 days, week, and month are the same compact icon switch, calendar-only. There is no drag, create, recurrence, or resource column.
- The list keeps five rows on the page and View more expands the same panel, capped, with the list scrolling inside. Calendar views do not clip: a day shows a few chips, then +N, and the overflow card lists the rest.
- HoverCard, not Tooltip: title, account, clock + day, status. Status is Soon / Later today / Ready from the meeting time against the clock, never a second API field. Chips and list rows mark it with a square vertical tone line only, never a fill, never coloured title, never a rounded chip. The badge still names it. Sweeping waits the 600ms family. Click is the verb; the card never grows an Open brief button.
- Flush inside the contained Coming up panel. Hairlines between cells, never a second panel. Period nav is ghost icon-sm and hides on the list. Today is weight, not a primary fill.
- Before the clock is read, the visible period comes from the soonest meeting so the server pass and the first client pass agree. Today is unmarked until `now` is a real time.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
