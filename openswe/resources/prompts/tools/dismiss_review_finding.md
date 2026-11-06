Dismiss one of Open SWE's review findings on a pull request, as the person you are working for.

Use this only when that person explicitly asks to dismiss a finding, or confirms that it is wrong or not worth fixing; never dismiss findings on your own judgment.
Find the finding's id with `list_review_findings`.
The dismissal is recorded with the finding's review, along with the person and the reason, and the finding's GitHub thread is resolved as them.
This is the same action as the review page's **Dismiss** button.

Args:
- finding_id: The finding's id (`f_…`) from `list_review_findings`.
- reason: Why it is dismissed, in the person's words; leave empty when they gave none.
- pr_url: The pull request's GitHub URL; leave empty to use this conversation's pull request.

Returns: `{success, finding_id, status, resolution_note}`, or `{success: false, error}`.
