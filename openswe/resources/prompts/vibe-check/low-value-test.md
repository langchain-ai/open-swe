Does `diff` add test functions that only assert things the test itself guarantees, such as that a result exists, that a mock returns or receives what the test set it up with, or that a constant has its value, or that compute the expected value with a formula instead of stating it?
If `diff` adds no test functions, the answer is no.

The added tests can't catch a bug: they check what the test set up, or recompute the answer instead of stating it.

Added tests compare results to stated expected values for meaningful inputs, edge cases, or errors, or no test functions are added.
