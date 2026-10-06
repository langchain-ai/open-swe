# ReviewBand

A review band presents one object to act on. It is not a list row, not a New-this-week card, and not a contained PageSection. Suggested play is a titled flat section; this band is the object inside it (`PAGE_SECTION_RULES`).

Import: `@langchain/gtm-platform-design-system/patterns/review-band`
Source: `src/patterns/review-band.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ReviewBand

| Prop | Type | Required |
|---|---|---|
| `actions` | `ReactNode` | no |
| `className` | `string` | no |
| `description` | `ReactNode` | yes |
| `descriptionId` | `string` | no |
| `mark` | `ReactNode` | no |
| `meta` | `ReactNode` | no |
| `render` | `ReactElement` | no |
| `title` | `ReactNode` | yes |
| `titleClassName` | `string` | no |
| `titleId` | `string` | no |

## The decisions this carries

- A review band presents one object to act on. It is not a list row, not a New-this-week card, and not a contained PageSection. Suggested play is a titled flat section; this band is the object inside it (`PAGE_SECTION_RULES`).
- Anatomy is fixed: optional leading mark, then title / description / meta in one stack, then optional trailing actions. Actions never sit in the title row. A 32px control in that stack is what made the title-to-copy gap larger than the copy-to-meta gap.
- The text stack uses one gap (`md`). Title-to-description and description-to-meta are the same interval. Do not insert a second stack or a justified header inside the body.
- Description is `text-body` (14/20). It is read, not scanned. Do not set it on `text-label` and do not add a `leading-*` override. Title defaults to `text-title` semibold; a conversation adapter lowers it through the shared `text-label` object-title class. Meta is `text-meta`.
- The panel is muted fill, one hairline, panel radius, `padding="lg"`, and `gap="xl"` between mark, body, and actions. That is the whole treatment. No shadow, no second fill, no tighter call-site override.
- A surface that needs this object renders ReviewBand. Reaching for Inline + IconWell + a custom stack to recreate it is a gap in this pattern only if the slots cannot express the object; grow the slots here, once.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
