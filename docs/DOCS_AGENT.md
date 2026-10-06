# Open SWE Docs

Open SWE Docs checks user-facing documentation against changes in source PRs.
Configure its **Documentation target** under **Open SWE Review**. Settings are
instance-wide and require an administrator. Each repository has independent
**Review** and **Docs** switches: enable code review only, docs only, or both.
Both capabilities run through the reviewer agent in one canonical thread per PR,
with prompts, tools and skills selected for the enabled capabilities.

Choose a docs repository and its target base branch. Enable Docs on each source
repository in the repository settings. Sources must belong to a routed Open SWE workspace. The docs repository
can be outside that workspace. Optionally provide a public HTTPS, streamable HTTP
docs MCP URL for current published documentation. The connection exposes only
read-only annotated tools or non-destructive search/fetch/read/get/list tools.
Authentication-bearing MCP URLs are rejected; use the existing MCP connection
settings for authenticated servers (authenticated docs connections are not yet
supported by the docs URL setting).

The GitHub App must be installed on all selected repositories in the configured
installation, with Contents read for sources, Contents write for docs, Pull
requests read/write, and Issues write for comments and the `skip-docs` label.
Checks write is optional; checks are informational and never block merging.
An unrestricted local `gh` OAuth token cannot substitute for the App credentials.
Docs runs currently require LangSmith managed sandboxes. For screenshots, include
Chromium in the source workspace's sandbox image/update script. The screenshot
tool uses a fresh browser profile and a DNS-pinned public HTTPS proxy inside the
sandbox; redirects and page assets cannot reach private addresses.

## Dispatch and opt-out

Signed PR events (open, ready for review, reopen, synchronize, edits, labels,
close, draft conversion), PR comments, and linked docs PR changes use the same
coordinator. The coding agent also invokes it after opening a source PR. Documentation only starts for enabled, open, non-draft source PRs without
`skip-docs`. Code review is independently enabled; its existing draft-review
preferences still apply. `skip-docs` disables docs in code, without disabling
code review. Code-only review retains its existing on-demand and automatic behavior.
Enabling Docs creates the `skip-docs` label in that source repository. Adding
that label, closing a PR, or converting it to draft invalidates/cancels active docs work;
if code review remains eligible, a code-only run replaces the combined run;
removing the label or marking the source ready starts a current check.
Source events retain Open SWE's existing public-repository organization gate.

Runs deduplicate across coding-agent hooks, webhook retries, and workers using a
shared source-PR lock and an input fingerprint. Changes to source base/head SHA, enabled capabilities, source body/title, linked
docs SHA/state, docs base, or settings invalidate prior evidence. PR synchronize
events own automatic updates on docs-enabled sources, so a separate push webhook
does not create a duplicate code-review run.
Store/API failures return 503 from the webhook so delivery failures remain visible and can be redelivered. If a run fails,
a subsequent PR event retries it; there is no separate periodic docs reconciler.
Changing settings cancels existing work; subsequent PR events use the new settings.

## Linking and results

Put any `https://github.com/<docs-owner>/<docs-repo>/pull/<number>` URL in the
source PR body, issue comment, inline comment or review body. URLs with `files`, `commits`, `checks`, query
strings or fragments also work. Links match the exact configured repository and
GitHub hostname and are validated through the GitHub API. Several docs PRs may
cover one source, and a docs PR may cover several sources. Draft docs PRs are
valid review input; only the source PR must be non-draft.

The agent chooses between the bundled `docs-review` and `docs-author` skills:

- Linked docs PRs are reviewed together against the pinned source change. Needed
  corrections are posted on affected docs PRs and linked from a source comment.
  The agent never edits linked PRs, including its own.
- Without links, unnecessary documentation produces a completed informational
  check. Needed documentation produces a draft PR in the configured docs repo,
  followed by a source comment with its URL. Existing branches/PRs are never
  overwritten. No PR is merged automatically.

Source, docs base, and each linked docs PR are available in separate checkouts in
one sandbox. All docs-base `AGENTS.md` files are loaded before the model runs;
trusted-base writing skills are available through the skills loader. PR-head
instructions are evidence rather than authoritative guidance. Source and linked
PR code is inspected as text and must not be executed. Public docs output must
omit private source identifiers and implementation details.

Reviewer threads that carry docs context are private and visible to administrators because either checkout may
contain private content. Detailed assessments stay in the private run; source
checks use a short informational result.

GitHub access inside the sandbox is read-only and limited to the two repositories.
The generic coding-tool HTTP capability is disabled for docs sandboxes. Gated
server-side terminal tools re-fetch eligibility, source SHA, links and settings
before publishing. Docs changes export through a contained path manifest: regular
documentation/config/image files only, no symlinks, hidden files, automation,
credentials or agent-instruction changes; at most 100 files, 2 MiB/file, 10 MiB total.
