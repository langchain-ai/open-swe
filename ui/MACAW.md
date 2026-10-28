# Macaw in the dashboard

Use the vendored `@langchain/macaw-components` and `@langchain/macaw-tokens` packages for shared UI.
Import components through their package subpaths.
Use semantic tokens for colors, including CSS passed to third-party renderers.

## Component discovery

The dashboard includes `@langchain/macaw-cli` as a development dependency.
Use Node.js 26 or newer for the CLI. Before choosing a component, run from the repository root:

```sh
pnpm --dir ui exec macaw search "<capability>" --json
pnpm --dir ui exec macaw inspect Button --json
pnpm --dir ui exec macaw list --json
```

The CLI reads the vendored component package’s catalog, so discovery matches the installed API.
Read its [component guidance](node_modules/@langchain/macaw-components/docs/DESIGN.md) and
[styling guidance](node_modules/@langchain/macaw-components/docs/STYLES.md) before implementing UI.

## UI conventions

Standard actions, fields, menus, checkboxes, badges, and progress indicators use Macaw.
Rich button content needs an explicit accessible name (`aria-label` or `aria-labelledby`); check wrapping, truncation, and keyboard focus when adapting the default button layout.
Menu rows use `@/components/MenuItem` to retain the dashboard's icon spacing.

These integrations retain their specialized behavior:

- Monaco for code editing, Lexical for the composer, and Pierre for diffs and file trees.
  Their surrounding controls use Macaw; Pierre's diff colors use Macaw tokens.
- Sonner for the imperative toast API, with Macaw icons and semantic styles in `@/components/Toaster`.
- Sandboxed artifact HTML and its in-frame annotation controls, which run in a separate document.
- The desktop's standalone setup form, which connects to the backend before loading the React dashboard and intentionally runs without JavaScript.
- The composer's hidden native file picker and `SecretInput`'s native password field.
  The latter uses Macaw's input style helpers and a labelled Macaw reveal button while preserving native password semantics and controlled visibility.
- Structural navigation/card links, interactive images, and inline disclosure controls inside navigation links or field labels.
  These preserve the host element's semantics; ordinary content links use Macaw `Link`.
