# Debug: say why a channel reply failed

**Temporary.** `_channels/delivery.py` `_post_direct` logged only the error type
(`channel reply failed (RuntimeError)`) when MDA's GitHub channel could not post a run's
answer in preview. It now also logs the channel name, its transport and the error message
(at most 500 characters), and sends the exception to the log collection sink.

Drop this change once the GitHub channel's reply failure is understood; the upstream
fix belongs in MDA (log a reason that carries no secrets).
