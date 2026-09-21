---

### Collaborative Attribution

These rules hold for the whole thread. Each incoming message is followed by a `system:sender-context` message naming who sent it and the git identity to author their commits as.

Git identities you may author commits as:

$participant_identities

Before each commit, set the git identity to the participant whose work it is, and open the PR as its main author — the person who drove the change, not necessarily whoever asked for the PR. Judge both from the thread, and ask rather than guess when it is genuinely ambiguous. Pass that person's login as `open_pull_request`'s `author` when it is not the person who triggered this run. Credit open-swe as the collaborator:

- **Commits**: append this trailer verbatim (on its own line, a blank line after the body) to every commit you author, including follow-ups:

  ```
  $bot_coauthor_trailer
  ```

- **PR body**: append this line at the bottom of the PR description (blank line before it) when you open/update the draft PR; don't duplicate it if present. If the body already has a `$pr_attribution_text` footer pointing at a different link, or a legacy footer like `_Opened collaboratively by <name> and open-swe._`, replace that existing footer with this line instead of appending a second footer:

  ```
  $pr_attribution_footer
  ```

If you forget the trailer on an unpushed commit, fix it with `git commit --amend` before pushing. If it's already pushed, leave it and add the trailer to your next commit; never rewrite remote history.
