---

### Collaborative Attribution

This message was sent by **$display_name**. Before each commit, set the git identity to the participant who inspired that commit — use your best judgment — and open the PR as the sender of the message that asked for it. Credit open-swe as the collaborator:

- **Commits**: append this trailer verbatim (on its own line, a blank line after the body) to every commit you author, including follow-ups:

  ```
  $bot_coauthor_trailer
  ```

- **PR body**: append this line at the bottom of the PR description (blank line before it) when you open/update the draft PR; don't duplicate it if present. If the body already has a `Made by [Open SWE]` footer pointing at a different link, or a legacy footer like `_Opened collaboratively by $display_name and open-swe._`, replace that existing footer with this line instead of appending a second footer:

  ```
  $pr_attribution_footer
  ```

If you forget the trailer on an unpushed commit, fix it with `git commit --amend` before pushing. If it's already pushed, leave it and add the trailer to your next commit; never rewrite remote history.
