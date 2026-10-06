# FadeText

FadeText says there is more. It never uses an ellipsis. The cut is a trailing mask, not a painted wash, so hover and selected fills stay honest.

Import: `@langchain/gtm-platform-design-system/ui/fade-text`
Source: `src/ui/fade-text.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### FadeText

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `className` | `string` | no |
| `lines` | `FadeTextLines` | no |
| `render` | `ReactElement` | no |

## The decisions this carries

- FadeText says there is more. It never uses an ellipsis. The cut is a trailing mask, not a painted wash, so hover and selected fills stay honest.
- Lines are 1, 2, or 3 of the meta rung (16px). Not a free max-height. Two is the preview default.
- This is not ScrollFadeContainer. A row preview does not scroll. A scrolling pane does not use this.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
