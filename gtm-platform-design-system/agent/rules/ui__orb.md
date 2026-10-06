# Orb

A call site names what the agent is DOING, never what the orb looks like. `activity` is the product's verb set; the library's nine animation names are not exported and must not be. A recurring product question gets one canonical answer,...

Import: `@langchain/gtm-platform-design-system/ui/orb`
Source: `src/ui/orb.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### Orb

| Prop | Type | Required |
|---|---|---|
| `activity` | `OrbActivity` | no |
| `size` | `OrbRung` | no |
| `label` | `string` | no |
| `className` | `string` | no |

### OrbStatus

| Prop | Type | Required |
|---|---|---|
| `activity` | `OrbActivity` | no |
| `size` | `OrbRung` | no |
| `caption` | `string` | no |
| `className` | `string` | no |
| `data-testid` | `string` | no |

## The decisions this carries

- A call site names what the agent is DOING, never what the orb looks like. `activity` is the product's verb set; the library's nine animation names are not exported and must not be. A recurring product question gets one canonical answer, and 'which orb for this tool' is that question -- ORB_STATE_FOR_ACTIVITY is the answer, with its reasoning attached.
- Never import `thinking-orbs` outside this file. It is the same closed-set argument as `ui/glyphs.ts` for Heroicons: one module names the library, everything else reaches it through the wrapper, and the vocabulary stays reviewable.
- There are two sizes because the library ships two hand-tuned designs, not a scale factor. `inline` sits beside text, `avatar` stands alone at thread scale. Never scale an orb with a transform or a width -- that is a different, blurrier drawing.
- An orb is on screen only while work is genuinely in flight, and unmounts the moment it settles. It is never an empty state, never a brand mark, never decoration on a settled surface. This is the boundary that makes the continuous animation a sanctioned exception to the one-duration motion law rather than a hole in it.
- The canvas is `aria-hidden`, always. Every shipped call site already owns a live region, and a second announcer naming the same step is the double-announcement failure `chat/tool-call-card.tsx` exists to prevent. When the orb IS the only sign work is running, pass `label` and get a real `sr-only` text node in a `role=status` wrapper -- text, because a live region announces text that appears in it and does not reliably announce an `aria-label` that changed.
- Reduced motion collapses the orb to a single static frame, and here it diverges from `ui/spinner.tsx` on purpose: a 16px rotation is not a vestibular trigger and stopping it would read as frozen, but forty dots orbiting across 64px is exactly the motion the media query removes. The still frame plus the label beside it still says 'working'.
- No radius, no fill, no shadow, no ring around an orb. It is drawn on a transparent canvas; containing it would spend the containment signal on a transient indicator and grow a shadow on something that is not pressable.
- The quiet thread mark is `OrbStatus`: the orb plus a StatusLabel of the activity verb (`Thinking…`), the pair the library's own site draws. The orb is the busy signal — the word does not pulse. A step that already has a title (tool-call-card, subagent) keeps Orb alone.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
