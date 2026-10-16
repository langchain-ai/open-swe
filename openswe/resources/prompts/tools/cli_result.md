Supply this run's result to the terminal that started the thread. The CLI prints `stdout` verbatim as its only output, then exits with `exit_code`. Nothing else you write reaches that terminal.

Call it as the last thing you do in every run, including runs that fail and runs that only answer a question. `stdout` is the complete answer, written for a terminal: plain text rather than Markdown, unless the user asked for a format.

`exit_code` follows the convention of `grep` and `test`, because scripts branch on it:

- `0`: the task was done, or the answer to a yes/no question is yes.
- `1`: the task failed, or the answer is no.
- `2`: the task could not be done or the question could not be answered, for example because the tests would not run.
