---
name: helm-template-review
description: Review Helm/Kubernetes chart or template PRs deterministically. Use when the diff touches charts/, modules/kubernetes/, templates/*.yaml, values.yaml, or environments/* — anything where the reviewable artifact is a rendered manifest rather than the raw template. Render each affected chart/environment once, diff the rendered manifests once, and reason over that single rendered diff instead of re-running raw git diff at escalating context widths or writing ad-hoc render/compare scripts.
---

# Helm / Kubernetes template review

The reviewable artifact for a Helm/Kubernetes-template PR is the **rendered
manifest**, not the raw template source. A change to a template, helper, or
`values.yaml` can be a no-op or a large behavioral change depending on how it
renders. Judge semantic equivalence from rendered output — computed **once** and
reused across every analysis pass.

Always invoke `gh` as `GH_TOKEN=dummy gh <command>`.

## Do this once, then reuse

1. **Locate changes with the diff you already fetched.** Use the raw unified
   diff only to find *which* charts, environments, and templates changed. Do NOT
   re-run `git diff` / `gh api .../compare` at multiple `--unified` widths to
   widen context, and do NOT repeatedly `git show` the same files — `read_file`
   the changed paths at the line ranges you need.

2. **Render each affected chart/environment once into a stable temp dir.** Use
   the repo's documented render command when one exists (check `Makefile`,
   `scripts/`, `README`, or CI); otherwise fall back to `helm template`:

   ```
   mkdir -p /tmp/helm-review/base /tmp/helm-review/head
   # for each affected chart + values/environment combination:
   helm template <release> <chart-dir> -f <values-or-environment-file> \
     > /tmp/helm-review/head/<chart>-<env>.yaml
   ```

   Render the base (pre-PR) side the same way from the base checkout or
   `git show <base_sha>:<path>`. Keep the output paths stable so you can reuse
   them; do not re-render for each new question.

3. **Diff the rendered manifests once per environment.**

   ```
   diff -u /tmp/helm-review/base/<chart>-<env>.yaml \
           /tmp/helm-review/head/<chart>-<env>.yaml
   ```

   This single rendered diff is your source of truth for semantic equivalence.
   Reason over it for every pass — resource shape, env vars, image tags,
   replicas, resource limits, RBAC, volume mounts. Do not re-render or re-diff to
   re-answer a follow-up question you can read off the manifest you already have.

## Rules

- Raw unified diffs are for **locating** changes only; equivalence is judged from
  rendered output.
- Do NOT write one-off per-review Python scripts (`python - <<PY ...`) to render
  or semantically compare templates, and never `pip install` a rendering
  dependency (e.g. PyYAML) mid-review. Use the render-once workflow above.
- If a render command fails or a chart cannot be rendered in the sandbox, say so
  and fall back to reading the templates and values directly — do not loop
  retrying escalating-context diffs.
- File findings under the same bar as any review: anchored to a changed line,
  concrete failure mode, in-diff. A rendered-manifest change that produces a
  broken or behavior-changing deployment is a finding; a purely cosmetic
  template edit that renders identically is not.
