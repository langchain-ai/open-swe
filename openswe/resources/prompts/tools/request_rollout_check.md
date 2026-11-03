Record that the person agreed to a rollout check of this change.

Call this when they agree to monitor before the pull request merges.
After it has merged, read `.agents/skills/rollout-check/SKILL.md` and follow its accepted-offer steps before deciding.
Call this only when those steps say to.
It does not run the check.
If the pull request is already merged, the thread is subscribed now to each deploy that includes the merge commit.
Otherwise that subscription happens when the pull request merges.
