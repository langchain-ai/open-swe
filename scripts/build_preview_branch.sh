#!/usr/bin/env bash
set -euo pipefail

PREVIEW_BRANCH="${PREVIEW_BRANCH:-preview}"
PREVIEW_LABEL="${PREVIEW_LABEL:-preview}"
PREVIEW_MANUAL_BRANCH="${PREVIEW_MANUAL_BRANCH:-preview-manual}"
PREVIEW_MAX_PRS="${PREVIEW_MAX_PRS:-50}"
PREVIEW_RESET_DAYS="${PREVIEW_RESET_DAYS:-7}"
PREVIEW_RESET_HOUR="${PREVIEW_RESET_HOUR:-7}"
PREVIEW_RESET_ZONE="${PREVIEW_RESET_ZONE:-America/New_York}"
PREVIEW_URL="${PREVIEW_URL:-https://open-swe-preview-cc53e8fbe667565d843d0843f84ee92c.us.langgraph.app/agents}"
PREVIEW_DASHBOARD_URL="${PREVIEW_DASHBOARD_URL:-https://dev.open-swe.langchain.dev/agents}"
PREVIEW_DASHBOARD_WORKFLOW_URL="${PREVIEW_DASHBOARD_WORKFLOW_URL:-https://github.com/langchain-ai/langchainplus/actions/workflows/deploy_open_swe_preview.yaml}"
# The LangSmith deployment that builds on every push to the preview branch.
PREVIEW_DEPLOYMENT_ID="${PREVIEW_DEPLOYMENT_ID:-c6ef2b27-439d-4926-9dd4-22d3af7cbdd4}"
PREVIEW_CONTROL_PLANE_URL="${PREVIEW_CONTROL_PLANE_URL:-https://api.host.langchain.com}"
PREVIEW_DEPLOY_TIMEOUT_MINUTES="${PREVIEW_DEPLOY_TIMEOUT_MINUTES:-30}"
PREVIEW_DEPLOY_POLL_SECONDS="${PREVIEW_DEPLOY_POLL_SECONDS:-30}"
PREVIEW_COMMENT_MARKER="<!-- preview-deployment -->"
CONFLICT_LIMIT=10

summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY" || printf 'warning: could not write the summary\n' >&2
  else
    printf '%s\n' "$1"
  fi
}

short_sha() {
  printf '%.7s' "$1"
}

fetch_main() {
  git fetch --no-tags origin main
}

open_preview_pulls() {
  local excluded="${EXCLUDE_PR_NUMBER:-0}"
  [[ "$excluded" =~ ^[0-9]+$ ]] || { printf 'invalid EXCLUDE_PR_NUMBER: %s\n' "$excluded" >&2; return 1; }
  gh api --paginate "repos/${GH_REPO}/pulls?state=open&per_page=100" \
    --jq ".[]
      | select(any(.labels[]; .name == \"${PREVIEW_LABEL}\"))
      | select(.number != (${excluded} | tonumber))
      | [.number, .head.sha, .author_association, .user.login, .html_url, .title]
      | @tsv" |
    sort -n |
    awk -v max="$PREVIEW_MAX_PRS" 'NR<=max'
}

manual_instructions() {
  local number="${1:-<pr>}"
  cat <<EOF
Merge the conflicting PRs together by hand and push the result as the
\`${PREVIEW_MANUAL_BRANCH}\` branch, instead of labelling them:

\`\`\`bash
git fetch origin main
git switch -c ${PREVIEW_MANUAL_BRANCH} origin/main
git fetch origin ${PREVIEW_MANUAL_BRANCH} && git merge FETCH_HEAD   # whatever is already there, if anything
git fetch origin pull/${number}/head && git merge FETCH_HEAD   # this one
git fetch origin pull/<other>/head && git merge FETCH_HEAD   # each one it clashes with
# resolve with whatever merge tool you like, then commit
git push origin ${PREVIEW_MANUAL_BRANCH}
\`\`\`

Then drop the \`${PREVIEW_LABEL}\` label from the PRs that branch contains. Every preview run
merges \`${PREVIEW_MANUAL_BRANCH}\` before it merges anything else, so your resolution is what lands.
There is one \`${PREVIEW_MANUAL_BRANCH}\` branch for everyone, which is why the recipe merges it in
rather than replacing it: the push stays a fast-forward, and a rejected push means someone else got
there first — merge theirs in and push again.

The preview resets to plain \`main\` every ${PREVIEW_RESET_DAYS} days, in the
$(printf '%02d' "$PREVIEW_RESET_HOUR"):00 ${PREVIEW_RESET_ZONE} hour: labels are removed and
\`${PREVIEW_MANUAL_BRANCH}\` is deleted. Re-push the branch to bring it back.
EOF
}

conflict_marker() {
  local sha="$1"
  shift
  { printf '%s\0' "$sha"; printf '%s\0' "$@" | sort -z; } |
    sha256sum |
    awk '{print "<!-- preview-conflict:" substr($1, 1, 16) " -->"}'
}

comment_and_unlabel() {
  local number="$1" sha="$2"
  shift 2
  local conflicts=("$@") marker comments body path
  marker="$(conflict_marker "$sha" "${conflicts[@]}")"
  if ! comments="$(gh api --paginate "repos/${GH_REPO}/issues/${number}/comments" --jq '.[].body')"; then
    printf 'warning: could not list comments on #%s\n' "$number" >&2
    return 1
  fi
  if [[ "$comments" != *"$marker"* ]]; then
    body="${marker}"$'\n'"### Left out of the ${GH_REPO} preview"$'\n\n'
    body+="This PR could not be merged into the preview tree, so the preview was built without it and the \`${PREVIEW_LABEL}\` label has been removed. Re-applying the label replays this conflict; the recipe below is the way in."$'\n\n'
    if ((${#conflicts[@]})); then
      body+="Conflicting files:"$'\n\n'
      for path in "${conflicts[@]:0:$CONFLICT_LIMIT}"; do body+="- \`${path}\`"$'\n'; done
      if ((${#conflicts[@]} > CONFLICT_LIMIT)); then body+="- …and $((${#conflicts[@]} - CONFLICT_LIMIT)) more"$'\n'; fi
      body+=$'\n'
    fi
    body+="$(manual_instructions "$number")"
    if ! gh api --method POST "repos/${GH_REPO}/issues/${number}/comments" -f body="$body" >/dev/null; then
      printf 'warning: could not comment on #%s\n' "$number" >&2
      return 1
    fi
  fi
  if gh api --method DELETE "repos/${GH_REPO}/issues/${number}/labels/${PREVIEW_LABEL}" >/dev/null; then
    return 0
  fi
  printf 'warning: could not unlabel #%s\n' "$number" >&2
  return 1
}

# One comment per PR carries its preview status; later runs edit it in place.
upsert_comment() {
  local number="$1" body="$2" comment_id
  if ! comment_id="$(gh api --paginate "repos/${GH_REPO}/issues/${number}/comments" \
    --jq ".[] | select(.body | contains(\"${PREVIEW_COMMENT_MARKER}\")) | .id" | head -1)"; then
    printf 'warning: could not list comments on #%s\n' "$number" >&2
    return 1
  fi
  if [[ -n "$comment_id" ]]; then
    gh api --method PATCH "repos/${GH_REPO}/issues/comments/${comment_id}" -f body="$body" >/dev/null && return 0
  else
    gh api --method POST "repos/${GH_REPO}/issues/${number}/comments" -f body="$body" >/dev/null && return 0
  fi
  printf 'warning: could not comment on #%s\n' "$number" >&2
  return 1
}

preview_comment() {
  local pr_sha="$1" preview_sha="$2" base_sha="$3" backend="$4"
  cat <<EOF
${PREVIEW_COMMENT_MARKER}
### In the ${GH_REPO} preview

This PR's \`$(short_sha "$pr_sha")\` is part of the preview tree published as \`${PREVIEW_BRANCH}\` @ \`$(short_sha "$preview_sha")\` (\`main\` @ \`$(short_sha "$base_sha")\`).

- Preview: <${PREVIEW_DASHBOARD_URL}>
- Backend: ${backend}
- Dashboard: rolls out on the next [Deploy open-swe preview](${PREVIEW_DASHBOARD_WORKFLOW_URL}) run (:04, :19, :34, :49), reachable on the LangChain network only.

A push to this PR takes it out of the preview until the \`${PREVIEW_LABEL}\` label is re-applied.
EOF
}

dropped_comment() {
  local new_sha="$1"
  cat <<EOF
${PREVIEW_COMMENT_MARKER}
### Out of the ${GH_REPO} preview

\`$(short_sha "$new_sha")\` was pushed after this PR was approved for the preview, so the \`${PREVIEW_LABEL}\` label was removed and the next preview build leaves this PR out. Re-apply the label to include the new head.
EOF
}

# Comment the preview link on every PR the published tree contains; the
# deployment status starts out pending and the wait step fills it in.
announce_publication() {
  local preview_sha="$1" base_sha="$2" entry
  shift 2
  for entry in "$@"; do
    upsert_comment "${entry%%:*}" "$(preview_comment "${entry#*:}" "$preview_sha" "$base_sha" \
      "⏳ revision building — this comment updates when it is live.")" || true
  done
}

merge_ref() {
  local sha="$1" message="$2"
  MERGE_CONFLICTS=()
  MERGE_REASON=
  if output="$(git merge --no-ff -m "$message" "$sha" 2>&1)"; then
    return 0
  fi
  mapfile -d '' -t MERGE_CONFLICTS < <(git diff --name-only --diff-filter=U -z || true)
  if ((${#MERGE_CONFLICTS[@]})); then
    git merge --abort
    return 1
  fi
  if ! git merge-base HEAD "$sha" >/dev/null 2>&1; then
    MERGE_REASON="could not be merged — ${output%%$'\n'*}"
    git merge --abort >/dev/null 2>&1 || true
    return 1
  fi
  git merge --abort >/dev/null 2>&1 || true
  printf 'merge %s failed: %s\n' "$(short_sha "$sha")" "$output" >&2
  return 2
}

build() {
  local force="${FORCE:-false}" base_sha published_tree assembled_tree
  local included=() included_prs=() skipped=() conflicted=false preview_sha
  local number head_sha association login url title ref fetched reason unlabelled path output status pulls

  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  fetch_main
  git checkout -B "$PREVIEW_BRANCH" origin/main
  base_sha="$(git rev-parse HEAD)"

  set +e
  git ls-remote --exit-code --heads origin "refs/heads/${PREVIEW_MANUAL_BRANCH}" >/dev/null 2>&1
  status=$?
  set -e
  if ((status == 0)); then
    ref="refs/preview-manual"
    if ! git fetch --no-tags --force origin "refs/heads/${PREVIEW_MANUAL_BRANCH}:${ref}"; then
      skipped+=("\`${PREVIEW_MANUAL_BRANCH}\` — could not fetch the branch")
    else
      fetched="$(git rev-parse "$ref")"
      if merge_ref "$fetched" "preview: merge branch ${PREVIEW_MANUAL_BRANCH}"; then
        included+=("\`${PREVIEW_MANUAL_BRANCH}\` — \`$(short_sha "$fetched")\`")
      else
        status=$?
        if ((status != 1)); then return 1; fi
        if ((${#MERGE_CONFLICTS[@]})); then
          reason="merge conflict with \`main\` — rebuild the branch"
          conflicted=true
        else
          reason="$MERGE_REASON"
        fi
        skipped+=("\`${PREVIEW_MANUAL_BRANCH}\` — ${reason}")
        for path in "${MERGE_CONFLICTS[@]:0:$CONFLICT_LIMIT}"; do
          skipped[-1]+=$'\n'"  - \`${path}\`"
        done
      fi
    fi
  elif ((status != 2)); then
    printf 'could not look up %s\n' "$PREVIEW_MANUAL_BRANCH" >&2
    return 1
  fi

  if ! pulls="$(open_preview_pulls)"; then
    printf 'could not list open pull requests\n' >&2
    return 1
  fi
  while IFS=$'\t' read -r number head_sha association login url title; do
    [[ -z "$number" ]] && continue
    case "$association" in
      OWNER | MEMBER) ;;
      *) skipped+=("[#${number} ${title}](${url}) — @${login} — author is \`${association}\`, not an org member"); continue ;;
    esac
    ref="refs/preview-prs/${number}"
    if ! git fetch --no-tags origin "pull/${number}/head:${ref}" >/dev/null 2>&1; then
      skipped+=("[#${number} ${title}](${url}) — @${login} — could not fetch the PR head")
      continue
    fi
    fetched="$(git rev-parse "$ref")"
    if [[ "$fetched" != "$head_sha" ]]; then
      skipped+=("[#${number} ${title}](${url}) — @${login} — head moved mid-run, rerun to pick it up")
      continue
    fi
    if merge_ref "$fetched" "preview: merge PR #${number} from @${login}"; then
      included+=("[#${number} ${title}](${url}) — @${login} — \`$(short_sha "$fetched")\`")
      included_prs+=("${number}:${fetched}")
      continue
    else
      status=$?
    fi
    if ((status != 1)); then return 1; fi
    reason="${MERGE_REASON:-merge conflict with the preview tree}"
    unlabelled=
    if comment_and_unlabel "$number" "$fetched" "${MERGE_CONFLICTS[@]}"; then
      unlabelled=" — \`${PREVIEW_LABEL}\` label removed"
    fi
    skipped+=("[#${number} ${title}](${url}) — @${login} — ${reason}${unlabelled}")
    for path in "${MERGE_CONFLICTS[@]:0:$CONFLICT_LIMIT}"; do
      skipped[-1]+=$'\n'"  - \`${path}\`"
    done
    ((${#MERGE_CONFLICTS[@]})) && conflicted=true
  done <<<"$pulls"

  summary "## Preview tree"
  summary ""
  summary "Deployed preview: <${PREVIEW_URL}>"
  summary "Base: \`main\` @ \`$(short_sha "$base_sha")\`"
  summary "Republish even when the preview tree is unchanged: $([[ "$force" == true ]] && echo yes || echo no)"
  summary ""
  summary "### Merged (${#included[@]})"
  summary ""
  if ((${#included[@]})); then for path in "${included[@]}"; do summary "- ${path}"; done; else summary "_preview is identical to main_"; fi
  summary ""
  summary "### Skipped (${#skipped[@]})"
  summary ""
  if ((${#skipped[@]})); then for path in "${skipped[@]}"; do summary "- ${path}"; done; else summary "_nothing skipped_"; fi
  if [[ "$conflicted" == true ]]; then
    summary ""
    summary "### Getting a conflicting change in"
    summary ""
    while IFS= read -r path; do summary "$path"; done < <(manual_instructions)
  fi

  assembled_tree="$(git rev-parse 'HEAD^{tree}')"
  published_tree=
  if git fetch --no-tags --force origin "${PREVIEW_BRANCH}:refs/preview-published" >/dev/null 2>&1; then
    published_tree="$(git rev-parse 'refs/preview-published^{tree}')"
  fi
  if [[ "$assembled_tree" == "$published_tree" && "$force" != true ]]; then
    summary ""
    summary "Preview tree unchanged (\`$(short_sha "$assembled_tree")\`) — nothing published."
    [[ -n "${GITHUB_OUTPUT:-}" ]] && printf 'changed=false\n' >>"$GITHUB_OUTPUT"
    return
  fi
  if [[ "$assembled_tree" == "$published_tree" ]]; then
    summary ""
    summary "Preview tree unchanged (\`$(short_sha "$assembled_tree")\`) — continuing because publication was forced."
    git commit --allow-empty -m "preview: force deployment"
  fi
  git push --force origin "HEAD:refs/heads/${PREVIEW_BRANCH}"
  preview_sha="$(git rev-parse HEAD)"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'changed=true\npreview_sha=%s\nbase_sha=%s\nincluded_prs=%s\n' \
      "$preview_sha" "$base_sha" "${included_prs[*]+"${included_prs[*]}"}" >>"$GITHUB_OUTPUT"
  fi
  announce_publication "$preview_sha" "$base_sha" ${included_prs[@]+"${included_prs[@]}"}
}

reset() {
  local local_date local_hour latest due=false labels_kept=false branch_kept=false number url title marker pulls status
  local_date="$(TZ="$PREVIEW_RESET_ZONE" date +%F)"
  local_hour="$(TZ="$PREVIEW_RESET_ZONE" date +%H)"
  if ((10#$local_hour != PREVIEW_RESET_HOUR)); then
    printf '%s %s is outside the reset hour.\n' "$(TZ="$PREVIEW_RESET_ZONE" date +%H:%M)" "$PREVIEW_RESET_ZONE"
    return
  fi
  latest="$(git ls-remote origin 'refs/preview-reset/*' | sed 's#.*refs/preview-reset/##' | sort | tail -1)"
  if [[ -z "$latest" ]] || (( $(date -u -d "$local_date" +%s) - $(date -u -d "$latest" +%s) >= PREVIEW_RESET_DAYS * 86400 )); then
    due=true
  fi
  if [[ "$due" != true ]]; then
    printf 'The %s reset was less than %d days ago.\n' "$latest" "$PREVIEW_RESET_DAYS"
    return
  fi

  summary "## ${PREVIEW_RESET_DAYS}-day reset"
  summary ""
  if ! pulls="$(gh api --paginate "repos/${GH_REPO}/pulls?state=open&per_page=100" --jq ".[] | select(any(.labels[]; .name == \"${PREVIEW_LABEL}\")) | [.number, .html_url, .title] | @tsv")"; then
    summary "- **no PR was unlabelled** — could not list open pull requests"
    labels_kept=true
    pulls=
  fi
  while IFS=$'\t' read -r number url title; do
    [[ -z "$number" ]] && continue
    if gh api --method DELETE "repos/${GH_REPO}/issues/${number}/labels/${PREVIEW_LABEL}" >/dev/null; then
      summary "- dropped \`${PREVIEW_LABEL}\` from [#${number} ${title}](${url})"
    else
      summary "- **kept \`${PREVIEW_LABEL}\` on [#${number} ${title}](${url})** — removal failed"
      labels_kept=true
    fi
  done <<<"$pulls"

  set +e
  git ls-remote --exit-code --heads origin "refs/heads/${PREVIEW_MANUAL_BRANCH}" >/dev/null 2>&1
  status=$?
  set -e
  if ((status == 0)); then
    if git push origin --delete "$PREVIEW_MANUAL_BRANCH"; then
      summary "- deleted \`${PREVIEW_MANUAL_BRANCH}\`"
    else
      summary "- **kept \`${PREVIEW_MANUAL_BRANCH}\`** — deletion failed"
      branch_kept=true
    fi
  elif ((status == 2)); then
    summary "_no \`${PREVIEW_MANUAL_BRANCH}\` branch_"
  else
    summary "- **\`${PREVIEW_MANUAL_BRANCH}\` may remain** — branch lookup failed"
    branch_kept=true
  fi
  if [[ "$labels_kept" == true || "$branch_kept" == true ]]; then
    printf 'Reset incomplete — leaving the %s marker unset so the next tick retries.\n' "$local_date"
    return
  fi
  fetch_main
  marker="refs/preview-reset/${local_date}"
  git push origin "origin/main:${marker}"
  while IFS=$'\t' read -r _ ref; do
    [[ "$ref" == "$marker" ]] || git push origin --delete "$ref" || printf 'warning: could not delete %s\n' "$ref" >&2
  done < <(git ls-remote origin 'refs/preview-reset/*')
}

# Follow the LangSmith revision built from the published tree until it is live
# (or fails), then finish the status line in every included PR's comment.
wait_for_backend() {
  local sha="${PREVIEW_SHA:?PREVIEW_SHA is required}" base_sha="${BASE_SHA:-}" entry status message revision code body
  local deadline=$(( $(date +%s) + PREVIEW_DEPLOY_TIMEOUT_MINUTES * 60 )) backend=
  read -r -a prs <<<"${INCLUDED_PRS:-}"
  if [[ -z "${LANGSMITH_API_KEY:-}" ]]; then
    backend="❔ status unknown — this workflow has no LANGSMITH_API_KEY to read the deployment."
  fi
  while [[ -z "$backend" ]]; do
    body="$(curl -sS --max-time 20 -o /dev/stdout -w '\n%{http_code}' \
      -H "X-Api-Key: ${LANGSMITH_API_KEY}" \
      "${PREVIEW_CONTROL_PLANE_URL}/v2/deployments/${PREVIEW_DEPLOYMENT_ID}/revisions?limit=20")" || body=$'\n000'
    code="${body##*$'\n'}"
    body="${body%$'\n'*}"
    case "$code" in
      200) ;;
      401 | 403) backend="❔ status unknown — the workflow's LANGSMITH_API_KEY cannot read the open-swe-preview deployment."; break ;;
      *) printf 'control plane answered %s; retrying\n' "$code" >&2 ;;
    esac
    if [[ "$code" == 200 ]]; then
      revision="$(jq -c --arg sha "$sha" '[.resources[] | select(.source_revision_config.repo_commit_sha == $sha)] | first // empty' <<<"$body")"
      status="$(jq -r '.status // "PENDING"' <<<"${revision:-null}")"
      case "$status" in
        DEPLOYED)
          backend="✅ live since $(date -u +%H:%M) UTC (revision \`$(jq -r .id <<<"$revision" | cut -c1-8)\`)."
          break ;;
        FAILED | INTERRUPTED)
          message="$(jq -r '.status_message // "no details"' <<<"$revision")"
          backend="❌ revision ${status}: ${message} — see open-swe-preview in LangSmith Deployments."
          break ;;
        *) printf '%s revision is %s\n' "$(short_sha "$sha")" "$status" ;;
      esac
    fi
    if (( $(date +%s) >= deadline )); then
      backend="⏰ not live after ${PREVIEW_DEPLOY_TIMEOUT_MINUTES} minutes — see open-swe-preview in LangSmith Deployments."
      break
    fi
    sleep "$PREVIEW_DEPLOY_POLL_SECONDS"
  done
  summary "Backend for \`$(short_sha "$sha")\`: ${backend}"
  for entry in ${prs[@]+"${prs[@]}"}; do
    upsert_comment "${entry%%:*}" "$(preview_comment "${entry#*:}" "$sha" "$base_sha" "$backend")" || true
  done
  [[ "$backend" == ✅* || "$backend" == ❔* ]]
}

case "${1:-build}" in
  build) build ;;
  reset) reset ;;
  wait) wait_for_backend ;;
  dropped) upsert_comment "${2:?pr number}" "$(dropped_comment "${3:?new head sha}")" ;;
  *) printf 'usage: %s [build|reset|wait|dropped <pr> <sha>]\n' "$0" >&2; exit 2 ;;
esac
