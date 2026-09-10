## Pull request to review

- repo: $repo_owner/$repo_name
- pr_number: $pr_number
- url: $pr_url
- base_sha: $base_sha
- head_sha: $head_sha
$overview_section$prior_section
Call `fetch_review_diff`, then inspect its sandbox file with `grep` and paginated `read_file` calls. Review using the ordered passes (mechanical grep → diff-line audit → security/auth if applicable → pipeline sweep → deep flow).

This is a first review — there are no existing findings recorded by you.$historical_guidance Record net-new issues with `add_finding`, call `list_findings` to rank and dedup, then `publish_review` once at the end.
