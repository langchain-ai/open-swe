---
name: html-artifacts
description: Author the HTML for a plan artifact, dashboard iframe, or Slack attachment — structure, design plan, available runtime, theming, and craft. Read this before writing HTML for save_plan, output_iframe, or slack_attach_html.
---

# HTML artifacts

`save_plan`, `output_iframe`, and `slack_attach_html` all publish a self-contained HTML artifact, and one contract covers all three.

Write the page content directly. When you omit `<html>`, `<head>`, and `<body>`, the tool wraps your content in that skeleton with a minimal CSS reset. Write a `<title>` yourself — a specific 2–4 word name for this page, not a summary or a category label. A Slack attachment is opened as a standalone file, so include the full skeleton there.

## Design plan first

Sketch a compact plan before writing HTML, then follow it:

- **Color:** 4–6 named hex values grounded in the subject, including deliberately tinted neutrals. A pure mid-grey reads as unconsidered; a grey biased toward the accent reads as chosen.
- **Type:** at least a display and a body role, plus a utility/data face when useful. Google Fonts may be linked directly; every face needs a real fallback stack.
- **Layout:** the layout concept in one or two sentences. A plan or memo is polished and utilitarian — most pages need no oversized landing-page hero.

Derive every color and type decision in the page from that plan.

## Runtime

Inline CSS and JavaScript, Canvas, WebGL, and inline SVG all run — prefer Canvas or WebGL over hand-authored SVG path data for generative graphics. Google Fonts stylesheets are the only permitted external resource; inline or data-URI every other asset.

Assume no network at runtime: no CDN scripts, `fetch`, or XHR. The viewer frames have an opaque origin, so `localStorage`, `sessionStorage`, and cookies throw on access — wrap any use in try/catch and render correctly with no stored value.

## Theming

The dashboard stamps `data-theme="light"` or `data-theme="dark"` on `<html>`, so `:root[data-theme="dark"]` is the authoritative dark layer and must be complete on its own.

```css
:root { /* the full light palette, as tokens */ }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* redefine only the tokens */ }
}
:root[data-theme="dark"] { /* redefine them again — this layer wins */ }
```

Declare `color-scheme` in each layer too (`light` on `:root`, `dark` in both dark layers) so scrollbars, form controls, and UA defaults follow the artifact instead of staying light under a dark palette. The dashboard seeds it from the viewer's theme; your own declaration wins.

The media-query layer covers surfaces that stamp nothing, such as a downloaded file or a Slack attachment. Paint `body` with an explicit token background and style components only through tokens: a color whose only definition sits inside a media or `[data-theme]` block is the classic unreadable-artifact bug. Give the second theme the same care as the first — don't naively invert; keep contrast legible and the accent working on both grounds. A deliberate single-theme artifact may omit both dark layers only when every background and foreground is explicit.

## Craft

Semantic structure and real content, never lorem. Keep running text near 65 characters, set a type scale and stay on it, give headings `text-wrap: balance`, uppercase labels a touch of letter-spacing, and columns of digits `font-variant-numeric: tabular-nums`.

Space sibling groups with flex or grid `gap` rather than per-element margins that collapse or double. Wide content — tables, code, diagrams — gets its own `overflow-x: auto` container so the body never scrolls sideways.

Keep keyboard focus visible, honor `prefers-reduced-motion`, close every non-void element, and watch selector specificity: classes that cancel each other out silently undo your spacing.

When the artifact is a tool or dashboard rather than a document, the craft shifts to information design. Surface the summary before the detail and encode state in form as well as number — a pill, a chip, a severity stripe — so what needs attention reads at a glance. Semantic color (good / warning / critical) is separate from the accent hue and does not count as your accent.

## Point of view

Ground visual choices in the task's subject and audience, and honor any design system already in the repo (`AGENTS.md`, a tokens or theme file, existing component styles) over your own choices.

Avoid generic AI defaults: cream/terracotta serif pages, black with one neon accent, purple-blue gradient heroes, broadsheet hairlines over dense columns, centered-everything layouts, ubiquitous rounded cards, decorative numbering, emoji section markers, and defaulting to Inter or Space Grotesk. Where the user names a visual direction, follow it exactly — including when they ask for one of these.

Structural devices — numbering, eyebrows, dividers, labels — must encode something true about the content. Numbered markers belong on a real sequence, not on an unordered list of points.

Write copy as design material: active voice, from the reader's side of the screen, specific over clever. A control says exactly what happens; an error says what went wrong and how to fix it.

For an editorial request — something the user will keep or share — review the design plan for choices you would produce for any similar page, revise at least one of them, spend boldness in one place, and keep everything around it quiet.
