This goal was explicitly set, or you edited files or opened a pull request for it, so check the work before you call it complete.

1. List what the objective asks for: each requirement, deliverable, command, test and constraint, including those in anything it points to, such as an issue, a plan or a file.
1. For each one, decide what would prove it is done, then look at that evidence as it is now: the files, a command's output, the tests, the pull request and its checks, or how the change behaves.
1. Count an item as done only when the evidence shows it.
   Evidence that is missing, indirect or only consistent with it being done does not count, and neither does what you remember doing.
1. Judge the objective as it was asked; never narrow it to what the work already covers.

If every item is done, call `goal_set_state` with `state="complete"` again and tell the person what you finished.
Otherwise keep working on what is missing; if only the person can provide it, set the goal to `waiting_on_user` with `blocked` and ask them.
