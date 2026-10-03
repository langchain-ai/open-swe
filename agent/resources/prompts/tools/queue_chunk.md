Prepare a chunk ahead of the reader and put it at the end of the queue, without showing it. It takes the same arguments as `show_chunk`, is rendered now, and holds its lines so nothing else claims them.

When the reader clicks "Looks good", the server shows the first queued chunk right away, so queue what they would most likely want next, in order.
