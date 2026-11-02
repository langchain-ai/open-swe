Supply this run's result to the terminal that started the thread.
The CLI prints `stdout` verbatim as its only output, then exits with `exit_code`.
Nothing else you write reaches that terminal.

Call it as the last thing you do in every run, including runs that fail and runs that only answer a question.
`stdout` is the complete answer, written for a terminal: plain text rather than Markdown, unless the user asked for a format.

While workers you launched are still running, the CLI does not exit: it keeps serving them, each worker's report wakes you in a new run, and only the result of the run that ends with no worker left running is printed.
So when you end a run before your workers finish, keep `stdout` to a short status; once they have all reported, call it with the complete answer.
When a run a worker's report or message woke has nothing new for the terminal, call it with `unchanged=true`: the terminal keeps the answer it already has, and `stdout` and `exit_code` are ignored.
An empty `stdout` without `unchanged` is an answer like any other.

`exit_code` follows the convention of `grep` and `test`, because scripts branch on it:

- `0`: the task was done, or the answer to a yes/no question is yes.
- `1`: the task failed, or the answer is no.
- `2`: the task could not be done or the question could not be answered, for example because the tests would not run.
