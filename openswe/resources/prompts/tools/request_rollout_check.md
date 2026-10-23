Record that the person agreed to a rollout check of this change.

Call this when they agree to monitor before the pull request merges. After it has merged, call this only when the rollout skill says a staging or production target still does not contain the commit. It does not run the check. If the pull request is already merged, the thread is subscribed now to each deploy that includes the merge commit. Otherwise that subscription happens when the pull request merges.
