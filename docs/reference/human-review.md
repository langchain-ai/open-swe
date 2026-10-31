# Human review in Slack

A human review request is a Slack card asking people to review one pull request.
It has two kinds, stored in one `human_review_request` table:

- **Standard** (this document): any open pull request, posted in the repository's review channel; people sign up and review on GitHub, and it merges once they approve.
- **Expedited** ([expedited-slack-review.md](expedited-slack-review.md)): a tiny pull request approved straight from the diff on the card.

One request per pull request is open at a time, whatever its kind.
Asking for an expedited review of a pull request with an open standard request replaces it; asking for a standard review while an expedited card is open returns its link and reports that it already satisfies the review request.
The agent must tell the requester review is in progress, not cancel the card to switch to standard review unless the requester explicitly asks.

## Configuration

`.open-swe/settings.json` in the repository:

```json
{ "reviewChannel": "#eng-reviews" }
```

The value is a channel name or id.
It is read from the pull request's head commit, falling back to the default branch when the head has no settings file.
The `request_human_review` tool accepts a `channel` that overrides it or stands in when the repository sets none.

PR authors must have an Open SWE account or be one of Open SWE's own bots.
To also accept specific GitHub users or apps, add their exact logins to `reviewAllowedAuthors`:

```json
{
  "reviewChannel": "#eng-reviews",
  "reviewAllowedAuthors": ["langsmith-engine[bot]"]
}
```

Logins match case-insensitively; wildcards are not supported.
The allow-list is read only from the repository's default branch, never from the PR head, and unreadable or invalid settings block unregistered authors.
It only permits requesting standard human review; it does not grant an Open SWE account or trusted-comment status, bypass draft or closed-PR checks, or relax merge requirements.
`EXTRA_INTERNAL_BOT_LOGINS` controls trusted comments separately and does not grant review eligibility.

For monorepos, add ordered path rules:

```json
{
  "reviewChannel": "#eng-reviews",
  "reviewChannelRules": [
    { "paths": ["ui/*", "desktop/*"], "channel": "#frontend-reviews" },
    { "paths": ["agent/*"], "channel": "#backend-reviews" }
  ]
}
```

Patterns match case-sensitive repository-relative paths; `*` includes nested paths.
The first matching rule assigns each changed file to a channel, and unmatched files do not count toward any channel when rules are configured.
If no rule matches, no review channel is available and the PR page hides the Slack review request button.
Without rules, `reviewChannel` remains the repo-wide destination.
The channel with the most changed files wins; ties are broken uniformly at random among the tied channels.
Files count equally, including tests.
The selected channel gets one canonical request, which stays there once posted.
Explicit channel overrides still take precedence.
Expedited cards use the same routing for their broadcast destination.

### Supplementary reviews

A repository can ask for specialist reviews, such as design or security, alongside engineering review.
Configure them on the default branch:

```json
{
  "supplementaryReviews": [
    {
      "id": "design",
      "label": "Design review",
      "team": "langchain-ai/product-design",
      "paths": ["smith-frontend/src/*"],
      "exclude": ["*.test.*", "*/__tests__/*"],
      "criteria": "New or redesigned screens, flows, navigation, or substantial layout and interaction changes. Not tests, refactors, or tiny styling tweaks."
    }
  ]
}
```

- `id` is a unique lowercase slug; `label` names the review in notifications.
- `team` is a GitHub `organization/team-slug`.
- `paths` and `exclude` use the same glob matching as review channel rules; a file counts when it matches `paths` and no `exclude` pattern.
- `criteria` is what the assessment judges the pull request against.

When standard or posted review starts, each policy whose paths the pull request touches gets an isolated, unlisted assessment.
If the criteria are met, an available team member is rotated in (least recently assigned for that policy, within working hours, honoring out-of-office and opt-outs).
For the `langchain-ai/product-design` team's `design` policy, edit `openswe/resources/design-review-owners.json` in Open SWE to maintain product ownership. The assessment selects the main affected area and passes `supplementary_review_area`; product-specific styling follows the product owner, while shared components, tokens and styling use `design-system`. A mapped owner who is out of office, has unknown availability, or is outside working hours waits rather than falling back to an unrelated designer. Permanently ineligible owners (for example the PR author, an existing reviewer, an opted-out user, or someone no longer on the team) fall back to team rotation, with a warning logged. Unmapped areas retain team rotation.
The reviewer receives a GitHub review request and one concise Slack ping: impact, diff size, and why they were selected, with up to two images already embedded in the PR description. If Slack rejects the images, delivery falls back to text. A DM is used only if the thread ping was not delivered; retries preserve successful delivery flags. No extra GitHub comment is posted.
Supplementary reviews never replace the engineering request's owning thread or summary, and a supplementary reviewer's approval does not count as engineering approval unless they also own the code or signed up as an engineering reviewer.
Agents can also ask for one with `request_human_review`'s `supplementary_review` and `supplementary_review_reason`.
Settings use the default branch's cached configuration; changes can take 30 minutes to refresh.

Workspace admins can override the two-hour auto-assignment wait through `PUT /dashboard/api/workspaces/{workspace}/settings` with `human_review_auto_assign_minutes` (a positive integer; `null` inherits the instance setting).
The instance default can be set through `PUT /dashboard/api/settings`.
This affects reviewer assignment, not the two-hour auto-merge deadline.

## Requesting

- Asking is a per-person feature flag, **Request human reviews in Slack** on the Feature Flags page (`human_review_requests`), off by default.
  Without it the dashboard hides its button and refuses the request, and the agent does not get `request_human_review` in runs that person starts.
  Signing up, dismissing, and the agent's pick of a reviewer work for everyone.
- The agent calls `request_human_review`; people use **Request review in Slack** on the dashboard's pull request views.
- Refused while the pull request is closed, a draft, conflicted, or failing a required check.
- From a Slack thread in the review channel, the card is a reply in that thread that is also sent to the channel.
  Otherwise it is a new message in the review channel.
- The card shows the pull request's number and title, a summary, its author, size and branches, who asked when that is not the author, and its reviewers once anyone signs up.
  The agent must pass the summary as `inline_summary`; a dashboard request shows the start of the PR description instead, cut at 280 characters with an ellipsis.
  Nothing is generated by a model.
- For an open standard request, asking again with an `inline_summary`, from the thread or by the person that made the request, replaces the card's summary; anyone else gets the existing card unchanged.
- The agent can take a request down with `dismiss_human_review_request`, the same as the card's Dismiss button; the card reads "dismissed by Open SWE" and any reason.
- The `oswe` MCP server offers `request_human_review` and `dismiss_human_review_request` to local coding sessions, through `POST /dashboard/api/repos/{owner}/{repo}/pulls/{number}/human-review` (with an optional `inline_summary` and `channel`) and `.../human-review/dismiss`.
  The card then names the signed-in person.

## Reviewers

- **Allow automatic review picks** in dashboard Settings controls whether Open SWE can automatically assign you, including fallback agent picks.
  It defaults to on and can also be changed with `save_user_settings` using `auto_human_review_enabled`.
  Turning it off does not prevent someone explicitly naming you as a reviewer or you signing up yourself.
- **I'll review** adds the clicker as a reviewer and requests their review on GitHub.
  Any number of people may sign up; they need write access and cannot be the author.
- **Dismiss** closes the card; anyone may click it.
- If nobody signs up within 2 hours (or the workspace’s configured timeout), the agent that asked is woken (for a dashboard request, the requester's pull request thread) to pick one from CODEOWNERS and the review history of the changed files.
  It calls `assign_human_reviewer`, which adds the person to the card, tags them in the card's thread, and DMs them.
  In concierge mode the DM is recorded in their concierge conversation.

## Merging

The request re-checks the pull request on every review and CI completion webhook, and two hours after it was posted.
It merges, as the GitHub App and never with an admin bypass, when:

- at least one approval is standing on GitHub, and
- every reviewer on the card has approved, or two hours have passed, and
- the pull request is otherwise ready: checks green, no conflicts, no unresolved threads, no standing change request, and an Open SWE review of the head where auto-review is on.

While it waits, the card says what it is waiting on.
A merged request's card collapses to one line and its message gets a `:merged:` reaction.
A card that was also sent to the channel leaves it when it closes, and the closed card stays in the thread.
