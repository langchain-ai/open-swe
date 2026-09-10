## A new commit has been pushed

- repo: $repo_owner/$repo_name
- pr_number: $pr_number
- url: $pr_url
- previous reviewed SHA: $last_reviewed_sha
- new HEAD SHA: $head_sha

$overview_section## Existing findings

$existing_findings

${prior_threads_section}Call `fetch_review_diff` to materialize the changes since the previous reviewed SHA, then inspect its sandbox file with `grep` and paginated `read_file` calls. Review only what's in that diff.

For each open finding above, decide whether the new commits resolved it (`update_finding(id, status="resolved", note="<full reply body>")`), left it unchanged (no action), or changed it materially (`update_finding` with new fields + a full reply-body `note`). If a human reply on a finding explains why your comment was invalid, verify that analysis, then call `resolve_finding_thread(id, status="dismissed", note="...")` to close it. The `note` is posted verbatim, so write it as the complete GitHub reply body. Reply only when directly asked or when a concise clarification is necessary. Then add any net-new findings introduced by the new diff — but skip anything already covered by an existing PR review thread above (your own prior threads, another reviewer's, or one a human has already replied to). Call `publish_review` once at the end.
