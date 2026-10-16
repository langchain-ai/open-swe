Record a review finding on the reviewer thread.

Findings persist on the reviewer thread's metadata so they survive sandbox
eviction and are queryable across runs by the watch-mode reconciliation
flow and the future UI.

**When to use:** Once per distinct issue you find while reviewing the
diff. Prefer one finding per issue, with a concise generated ``title`` that
names the failure mode, a clear ``description`` body, and, when you can
offer a concrete fix, a ``suggestion`` that exactly replaces lines
``start_line..end_line``.

**In-diff only:** ``start_line..end_line`` must be inside the PR diff.
Findings anchored to lines outside the diff are rejected (out-of-diff
findings are disabled). File-level findings (both ``start_line`` and
``end_line`` None) are accepted but won't render as inline GitHub
comments — only use when the issue truly isn't anchored to a line.

Args:
    severity: One of ``low``, ``medium``, ``high``, ``critical``.
    confidence: One of ``low``, ``medium``, ``high``.
    category: Short category label (``correctness``, ``security``, ``perf``,
        ``style``, ``flag``, etc.). Free-form; used for grouping in the UI.
    file: Repo-relative path of the file the finding refers to.
    title: Concise generated headline for the finding. Name the failure mode
        in roughly 4-10 words; do not copy or truncate the description.
    description: Markdown body the user sees. Do not repeat ``title`` as the
        first line.
    start_line: 1-based line in the new (post-PR) file where the
        relevant range begins. For a single-line finding, this is the
        line the issue is about. For a multi-line finding, this is the
        first line of the relevant range. Omit (with ``end_line``) for
        file-level findings.
    end_line: 1-based line where the relevant range ends. GitHub
        anchors the inline comment at ``end_line`` and renders the
        ``start_line..end_line`` span as the highlighted snippet, so
        choose ``end_line`` as the *last* line that matters — typically
        the line the comment is most directly about. For a single-line
        finding, set ``end_line == start_line`` (or omit it). Prefer
        the natural range of the issue over a single line: GitHub
        shows context above ``end_line``, so a one-line anchor often
        buries the issue under unrelated context. Defaults to
        ``start_line`` when omitted.
    suggestion: Replacement text for ``start_line..end_line``. When set,
        the published GitHub comment includes a ```suggestion``` block so
        the user can click "Commit suggestion". **Only set this for small,
        obvious fixes that fit in 4 lines or fewer** (e.g. a one-liner
        rename, a missing guard, a typo). Longer suggestions are dropped
        because they read as rewrites rather than reviews — leave those
        cases as a description-only finding so the author can decide how
        to fix it.
    side: ``RIGHT`` (post-PR file, default) or ``LEFT`` (base file). Almost
        always ``RIGHT``.

Returns:
    Dictionary with ``success``, ``finding_id`` and (on rejection) ``error``.
