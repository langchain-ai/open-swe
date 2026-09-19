Datadog Logs SQL dialect rules:

- `GROUP BY` and `ORDER BY` must repeat the full expression; do not use a `SELECT` alias. Correct: `SELECT DATE_TRUNC('minute', timestamp) AS minute, count(*) FROM logs GROUP BY DATE_TRUNC('minute', timestamp) ORDER BY DATE_TRUNC('minute', timestamp)`. Incorrect: `SELECT DATE_TRUNC('minute', timestamp) AS minute, count(*) FROM logs GROUP BY minute ORDER BY minute`.
- Percentile aggregates such as `p90()` and `P90()` are unsupported. Use `max()` or `avg()`, or use the Datadog metrics tool when a percentile is required. Correct: `SELECT max(duration) FROM logs`. Incorrect: `SELECT p90(duration) FROM logs`.
- `@`-prefixed log attributes must be double-quoted and each must also be declared in `extra_columns` with its type. Correct: `SELECT "@http.status_code" FROM logs` with `extra_columns` containing `{"name": "@http.status_code", "type": "string"}`. Incorrect: `SELECT source, duration FROM logs` or `SELECT @http.status_code FROM logs`.
