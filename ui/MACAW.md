# Macaw in the dashboard

Use the vendored `@langchain/macaw-components` and `@langchain/macaw-tokens` packages for shared UI.
Import components through their package subpaths.
Use semantic tokens for colors, including CSS passed to third-party renderers.

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
