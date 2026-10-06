# ProviderMark

Provider identity is a real brand mark in an IconWell, not an initial tile and not a bare SVG. Inbox rows, tool activity, receipts, and thread-rail HoverCard previews name the channel with this module. The thread-rail row itself does not...

Import: `@langchain/gtm-platform-design-system/patterns/provider-mark`
Source: `src/patterns/provider-mark.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ProviderMark

| Prop | Type | Required |
|---|---|---|
| `provider` | `ProviderId` | yes |
| `className` | `string` | no |
| `label` | `string` | no |
| `data-testid` | `string` | no |

## The decisions this carries

- Provider identity is a real brand mark in an IconWell, not an initial tile and not a bare SVG. Inbox rows, tool activity, receipts, and thread-rail HoverCard previews name the channel with this module. The thread-rail row itself does not: a 24px well grows the 32px control rung, so Slack origin there is a leading 16px muted well.
- Native brand colour is always on for ProviderMark. The one sanctioned mono exception is dense Settings feature rows, which use `ProviderLogo branded={false}` under SETTINGS_PRODUCT_RULES / surface-decisions §8e. Do not invent a second mono path.
- The well is 24px (`IconWell`); the logo is 14px inside it. Growing or shrinking either invents a second density.
- Unknown providers and `web` fall back to the Globe glyph in the same well. Inventing a new brand colour at a call site is how the palette drifts.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
