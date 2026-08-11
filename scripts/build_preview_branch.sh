#!/usr/bin/env bash
# Rebuild the `preview` deploy branch as main + every open PR carrying the
# preview label, in ascending PR order, restricted to org members. Runs only
# from main's checkout, and never executes anything from the PRs it merges.
set -euo pipefail

PREVIEW_BRANCH="${PREVIEW_BRANCH:-preview}"
PREVIEW_LABEL="${PREVIEW_LABEL:-preview}"
PREVIEW_MAX_PRS="${PREVIEW_MAX_PRS:-50}"

MARKER_PREFIX="<!-- open-swe-preview-branch"

summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
  else
    printf '%s\n' "$1"
  fi
}

# Post or update the single preview-branch comment on a PR. The state is
# encoded in the marker so an unchanged status is left alone rather than
# rewritten on every rebuild.
upsert_pr_comment() {
  local number="$1" state="$2" body="$3"
  local marker="${MARKER_PREFIX} state=${state} -->"
  local full_body existing existing_id existing_marker

  full_body="${marker}"$'\n'"${body}"

  existing="$(gh api --paginate "repos/${GH_REPO}/issues/${number}/comments" \
    --jq ".[] | select(.body != null and (.body | startswith(\"${MARKER_PREFIX}\"))) | [.id, (.body | split(\"\n\")[0])] | @tsv" |
    head -n 1)"
  existing_id="${existing%%$'\t'*}"
  existing_marker="${existing#*$'\t'}"

  if [[ -n "$existing_id" && "$existing_marker" == "$marker" ]]; then
    return 0
  fi

  if [[ -n "$existing_id" ]]; then
    gh api --method PATCH "repos/${GH_REPO}/issues/comments/${existing_id}" \
      -f body="$full_body" >/dev/null
  else
    gh api --method POST "repos/${GH_REPO}/issues/${number}/comments" \
      -f body="$full_body" >/dev/null
  fi
}

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git fetch origin main
git checkout -B "$PREVIEW_BRANCH" origin/main
base_sha="$(git rev-parse HEAD)"

# The REST pulls endpoint is the only one carrying author_association alongside
# the head sha; `gh pr list --json` does not expose it.
prs="$(gh api --paginate "repos/${GH_REPO}/pulls?state=open&per_page=100" \
  --jq ".[]
        | select(any(.labels[]; .name == \"${PREVIEW_LABEL}\"))
        | [.number, .head.sha, .author_association, .user.login]
        | @tsv" |
  sort -n |
  awk -v max="$PREVIEW_MAX_PRS" 'NR<=max')"

included=()
skipped=()

while IFS=$'\t' read -r number head_sha association login; do
  [[ -z "$number" ]] && continue

  case "$association" in
    OWNER | MEMBER) ;;
    *)
      skipped+=("#${number} (@${login}) — author is \`${association}\`, not an org member")
      upsert_pr_comment "$number" "not-member:${association}" \
        "This PR is labeled \`${PREVIEW_LABEL}\` but was **not** merged into the \`${PREVIEW_BRANCH}\` branch: only PRs authored by organization members are deployed to the preview environment."
      continue
      ;;
  esac

  if ! git fetch --no-tags origin "pull/${number}/head:refs/preview-prs/${number}" 2>/dev/null; then
    skipped+=("#${number} (@${login}) — could not fetch PR head")
    upsert_pr_comment "$number" "fetch-failed" \
      "Could not fetch this PR's head commit while rebuilding the \`${PREVIEW_BRANCH}\` branch, so it is **not** deployed to the preview environment."
    continue
  fi

  fetched_sha="$(git rev-parse "refs/preview-prs/${number}")"
  if [[ "$fetched_sha" != "$head_sha" ]]; then
    # Transient: a push landed mid-run and will retrigger this workflow.
    skipped+=("#${number} (@${login}) — head moved mid-run, will pick up on the next build")
    continue
  fi

  if git merge --no-ff -m "preview: merge PR #${number} from @${login}" "$fetched_sha"; then
    included+=("#${number} (@${login}) — \`${fetched_sha:0:7}\`")
    upsert_pr_comment "$number" "merged:${fetched_sha:0:7}" \
      "Merged into the \`${PREVIEW_BRANCH}\` branch at \`${fetched_sha:0:7}\` and deployed to the preview environment."
  else
    git merge --abort
    skipped+=("#${number} (@${login}) — merge conflict with the current preview branch")
    upsert_pr_comment "$number" "conflict:${fetched_sha:0:7}" \
      "This PR conflicts with the \`${PREVIEW_BRANCH}\` branch (\`main\` plus the other PRs labeled \`${PREVIEW_LABEL}\` with a lower number), so it is **not** deployed to the preview environment. Rebase on \`main\` or wait for the conflicting PR to merge, then push to retry."
  fi
done < <(printf '%s\n' "$prs")

git push --force origin "HEAD:refs/heads/${PREVIEW_BRANCH}"

summary "## Preview branch rebuilt"
summary ""
summary "Base: \`main\` @ \`${base_sha:0:7}\`"
summary ""
if ((${#included[@]})); then
  summary "### Merged (${#included[@]})"
  for entry in "${included[@]}"; do summary "- ${entry}"; done
else
  summary "### Merged (0)"
  summary "- _preview is identical to main_"
fi
if ((${#skipped[@]})); then
  summary ""
  summary "### Skipped (${#skipped[@]})"
  for entry in "${skipped[@]}"; do summary "- ${entry}"; done
fi
