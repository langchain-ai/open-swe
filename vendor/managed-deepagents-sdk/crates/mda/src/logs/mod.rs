//! `mda logs` — read the Agent Server logs of a deployed Managed Deep Agent.
//!
//! The command resolves the deployment the same way `mda deploy` does (the
//! authored agent `name`, or `-n`), fetches the most recent lines, and then
//! either exits or keeps streaming:
//!
//! - a terminal on stdout gets a live tail (poll, print what is new, repeat);
//! - a pipe or file gets the recent lines once, so `mda logs > agent.log` and
//!   `mda logs | grep ERROR` terminate.
//!
//! Output goes to stdout as plain lines; status messages go to stderr so the
//! stream stays greppable.

use std::collections::HashSet;
use std::io::{self, IsTerminal, Write};
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::agent_config;
use crate::command::CommandCompletion;
use crate::deploy::langsmith_context::{LangSmithContext, WORKSPACE_FLAG_HINT};
use crate::deploy::{env_file, resolve_api_key, resolve_deployment_id};
use crate::install::Installation;
use crate::interrupt::Interrupt;
use crate::langsmith::{LogEntry, LogLevel, LogOrder, LogQuery, LogTimestamp};
use crate::naming::normalize_name;
use crate::telemetry::update_facts;

/// Delay between polls while following.
const FOLLOW_INTERVAL: Duration = Duration::from_secs(3);

/// How many entries each follow poll asks for. Bursts larger than this are
/// drained over consecutive polls rather than dropped, so this only bounds how
/// much a single request returns.
const FOLLOW_LIMIT: u32 = 500;

/// Consecutive poll failures tolerated before giving up on a tail. Transient
/// network blips should not end a long-running `mda logs -f`.
const FOLLOW_MAX_FAILURES: u32 = 5;

/// User-facing options for `mda logs`.
pub struct LogsOptions {
    /// Path to the Managed Deep Agent project, used to resolve the deployment
    /// name and read `.env`.
    pub path: PathBuf,
    /// Deployment name override (defaults to the authored agent `name`).
    pub name: Option<String>,
    /// How many recent lines to fetch before streaming.
    pub lines: u32,
    /// Minimum severity to show.
    pub level: Option<LogLevel>,
    /// Whether to keep streaming. `None` follows only when stdout is a terminal.
    pub follow: Option<bool>,
    /// Workspace ID override (takes precedence over `LANGSMITH_WORKSPACE_ID`).
    pub workspace_id: Option<String>,
    /// Host backend URL override.
    pub host_url: Option<String>,
}

/// Run the logs command end-to-end.
#[expect(clippy::print_stderr, reason = "log stream status and retry messages")]
pub fn run(opts: LogsOptions) -> Result<CommandCompletion> {
    let env_path = opts.path.join(".env");
    let env_vars = env_file::parse_env_file(&env_path);
    let name = resolve_deployment_name(&opts)?;
    update_facts(|facts| facts.deployment.deployment_name = Some(name.clone()));
    let ctx = LangSmithContext::resolve(
        &env_vars,
        opts.host_url.as_deref(),
        opts.workspace_id.as_deref(),
        WORKSPACE_FLAG_HINT,
        |platform_api_url| resolve_api_key(&env_vars, &env_path, platform_api_url),
    )?;
    let client = ctx.client()?;
    let deployment_id = resolve_deployment_id(&client, &name)?;
    update_facts(|facts| facts.deployment.deployment_id = Some(deployment_id.clone()));

    let recent = client
        .deployment_logs(
            &deployment_id,
            &LogQuery {
                limit: opts.lines,
                order: LogOrder::Newest,
                start_time: None,
                level: opts.level,
            },
        )
        .with_context(|| format!("failed to read logs for '{name}'"))?;

    // The backlog is fetched newest-first so the limit keeps the *latest*
    // lines; print it chronologically.
    let mut entries = recent.logs;
    entries.reverse();

    let follow = opts.follow.unwrap_or_else(|| io::stdout().is_terminal());
    let mut stdout = io::stdout().lock();
    let Some(mut printed) = emit(&mut stdout, &entries)? else {
        return Ok(CommandCompletion::Succeeded);
    };

    if !follow {
        if entries.is_empty() {
            eprintln!("No logs found for '{name}'.");
        }
        return Ok(CommandCompletion::Succeeded);
    }

    if entries.is_empty() {
        eprintln!("No logs yet for '{name}'. Waiting for new output — press Ctrl-C to stop.");
    } else {
        eprintln!("Tailing logs for '{name}' — press Ctrl-C to stop.");
    }

    let interrupt = Interrupt::install()?;
    let mut since = entries.last().and_then(query_timestamp);
    let mut failures = 0_u32;
    // A poll that fills its page means more may already be waiting behind it, so
    // the next one runs immediately instead of after the interval.
    let mut draining = false;
    loop {
        if !draining {
            interrupt.sleep_until_interrupted(FOLLOW_INTERVAL);
        }
        if interrupt.is_requested() {
            return Ok(CommandCompletion::Succeeded);
        }

        let page = match client
            .deployment_logs(&deployment_id, &follow_query(since.as_deref(), opts.level))
        {
            Ok(page) => page,
            Err(err) => {
                failures += 1;
                if failures >= FOLLOW_MAX_FAILURES {
                    return Err(err).with_context(|| {
                        format!("lost connection while tailing logs for '{name}'")
                    });
                }
                eprintln!("warning: could not read logs ({err:#}); retrying");
                draining = false;
                continue;
            }
        };
        failures = 0;

        let poll = next_poll(page.logs, &printed);
        draining = poll.draining;
        if poll.fresh.is_empty() {
            continue;
        }

        let Some(keys) = emit(&mut stdout, &poll.fresh)? else {
            return Ok(CommandCompletion::Succeeded);
        };
        advance_cursor(&mut printed, &mut since, &poll.fresh, keys);
    }
}

/// Move the follow cursor past a printed batch.
///
/// When the batch ends on a newer timestamp, only its own keys can reappear in
/// the next inclusive poll. When it ends on the same timestamp as `since`, the
/// entries printed earlier at that timestamp come back too, so keep their keys.
fn advance_cursor(
    printed: &mut HashSet<String>,
    since: &mut Option<String>,
    fresh: &[LogEntry],
    keys: HashSet<String>,
) {
    let next_since = fresh.last().and_then(query_timestamp);
    if next_since.is_some() && next_since != *since {
        *printed = keys;
        *since = next_since;
    } else {
        printed.extend(keys);
    }
}

/// Build the request for one follow poll.
///
/// Unlike the backlog fetch, follow polls read oldest-first. A burst larger than
/// [`FOLLOW_LIMIT`] then returns its *earliest* slice, and advancing `since`
/// walks forward through the rest. Asking for the newest slice instead would
/// skip every record behind it.
fn follow_query(since: Option<&str>, level: Option<LogLevel>) -> LogQuery<'_> {
    LogQuery {
        limit: FOLLOW_LIMIT,
        order: LogOrder::Oldest,
        start_time: since,
        level,
    }
}

/// What one follow poll yields.
struct Poll {
    /// Entries not printed by an earlier poll, oldest first.
    fresh: Vec<LogEntry>,
    /// Whether to poll again immediately instead of waiting an interval.
    draining: bool,
}

/// Drop the entries a previous poll already printed, and report whether the page
/// came back full — meaning more of the same burst may already be waiting.
fn next_poll(page: Vec<LogEntry>, printed: &HashSet<String>) -> Poll {
    let was_full = page.len() as u32 >= FOLLOW_LIMIT;
    // `start_time` is inclusive, so every poll re-reads the entries sharing the
    // oldest timestamp. Those are exactly what the previous poll printed last.
    let fresh: Vec<LogEntry> = page
        .into_iter()
        .filter(|entry| !printed.contains(&dedupe_key(entry)))
        .collect();
    // A full page of nothing but duplicates cannot advance `since`, so back off
    // rather than spinning on an identical request.
    let draining = was_full && !fresh.is_empty();
    Poll { fresh, draining }
}

/// Resolve the deployment name from `-n`, else the authored agent `name`.
///
/// Loading the project is only required for the second case, so `mda logs -n
/// my-agent` works from any directory.
fn resolve_deployment_name(opts: &LogsOptions) -> Result<String> {
    if let Some(name) = &opts.name {
        return normalize_name(name);
    }
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;
    agent_config::agent_assistant_id(&project)
}

/// Write log lines, returning the dedupe keys of everything written, or `None`
/// when the reader closed the pipe (for example `mda logs | head`).
fn emit(out: &mut impl Write, entries: &[LogEntry]) -> Result<Option<HashSet<String>>> {
    let mut keys = HashSet::with_capacity(entries.len());
    // Flushing once per batch keeps a tail responsive (a batch is one poll)
    // without paying a syscall per line when dumping a large backlog.
    let written = entries
        .iter()
        .try_for_each(|entry| {
            writeln!(out, "{}", format_entry(entry))?;
            keys.insert(dedupe_key(entry));
            Ok(())
        })
        .and_then(|()| out.flush());

    match written {
        Ok(()) => Ok(Some(keys)),
        Err(err) if err.kind() == io::ErrorKind::BrokenPipe => Ok(None),
        Err(err) => Err(err).context("failed to write logs to stdout"),
    }
}

fn format_entry(entry: &LogEntry) -> String {
    let timestamp = display_timestamp(entry);
    let level = if entry.level.is_empty() {
        "INFO"
    } else {
        entry.level.as_str()
    };
    format!("{timestamp}  {level:<8}  {}", entry.message)
}

/// Identify an entry across overlapping polls. Ids are empty on backends that
/// cannot supply one, so fall back to the content of the line.
fn dedupe_key(entry: &LogEntry) -> String {
    if entry.id.is_empty() {
        format!("{}|{}", raw_timestamp(entry), entry.message)
    } else {
        entry.id.clone()
    }
}

/// RFC 3339 timestamp to send as the next poll's inclusive lower bound.
fn query_timestamp(entry: &LogEntry) -> Option<String> {
    match &entry.timestamp {
        LogTimestamp::EpochMillis(millis) => Some(rfc3339_from_epoch_millis(*millis)),
        LogTimestamp::Text(text) if !text.is_empty() => Some(text.clone()),
        LogTimestamp::Text(_) => None,
    }
}

fn display_timestamp(entry: &LogEntry) -> String {
    query_timestamp(entry).unwrap_or_else(|| "-".to_string())
}

fn raw_timestamp(entry: &LogEntry) -> String {
    match &entry.timestamp {
        LogTimestamp::EpochMillis(millis) => millis.to_string(),
        LogTimestamp::Text(text) => text.clone(),
    }
}

/// Format epoch milliseconds as a UTC RFC 3339 timestamp.
pub(crate) fn rfc3339_from_epoch_millis(millis: i64) -> String {
    let seconds = millis.div_euclid(1000);
    let subsecond = millis.rem_euclid(1000);
    let (year, month, day) = civil_from_days(seconds.div_euclid(86_400));
    let second_of_day = seconds.rem_euclid(86_400);
    let (hour, minute, second) = (
        second_of_day / 3600,
        (second_of_day % 3600) / 60,
        second_of_day % 60,
    );
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{subsecond:03}Z")
}

/// Convert days since the Unix epoch into a proleptic Gregorian date.
///
/// Port of Howard Hinnant's `civil_from_days`, which shifts the year to start
/// in March so the leap day lands at the end of the 146,097-day era cycle.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let march_month = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * march_month + 2) / 5 + 1;
    let month = if march_month < 10 {
        march_month + 3
    } else {
        march_month - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_deployment_name_rejects_ambiguous_explicit_name() {
        let options = LogsOptions {
            path: PathBuf::from("."),
            name: Some("foo--bar".to_string()),
            lines: 100,
            level: None,
            follow: None,
            workspace_id: None,
            host_url: None,
        };

        let error = resolve_deployment_name(&options).unwrap_err().to_string();

        assert!(error.contains("foo-bar"), "{error}");
    }

    fn entry(id: &str, timestamp: LogTimestamp, level: &str, message: &str) -> LogEntry {
        LogEntry {
            id: id.to_string(),
            timestamp,
            level: level.to_string(),
            message: message.to_string(),
        }
    }

    #[test]
    fn epoch_millis_render_as_utc_rfc3339() {
        assert_eq!(rfc3339_from_epoch_millis(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(
            rfc3339_from_epoch_millis(1_769_472_000_000),
            "2026-01-27T00:00:00.000Z"
        );
        // 2024-02-29 exercises the leap-day path.
        assert_eq!(
            rfc3339_from_epoch_millis(1_709_209_845_123),
            "2024-02-29T12:30:45.123Z"
        );
    }

    #[test]
    fn entries_render_timestamp_level_and_message() {
        assert_eq!(
            format_entry(&entry(
                "a",
                LogTimestamp::EpochMillis(1_769_472_000_000),
                "ERROR",
                "boom"
            )),
            "2026-01-27T00:00:00.000Z  ERROR     boom"
        );
    }

    #[test]
    fn entries_without_a_level_render_as_info() {
        assert_eq!(
            format_entry(&entry("a", LogTimestamp::Text(String::new()), "", "hello")),
            "-  INFO      hello"
        );
    }

    #[test]
    fn dedupe_falls_back_to_content_when_the_backend_sends_no_id() {
        let with_id = entry("insert-1", LogTimestamp::EpochMillis(1), "INFO", "same");
        let without_id = entry("", LogTimestamp::EpochMillis(1), "INFO", "same");
        assert_eq!(dedupe_key(&with_id), "insert-1");
        assert_eq!(dedupe_key(&without_id), "1|same");
    }

    #[test]
    fn emit_reports_written_keys_and_stops_cleanly_on_a_closed_pipe() {
        let entries = vec![
            entry("a", LogTimestamp::EpochMillis(1), "INFO", "one"),
            entry("b", LogTimestamp::EpochMillis(2), "INFO", "two"),
        ];

        let mut buffer = Vec::new();
        let keys = emit(&mut buffer, &entries).unwrap().unwrap();
        assert_eq!(keys, HashSet::from(["a".to_string(), "b".to_string()]));
        assert_eq!(String::from_utf8(buffer).unwrap().lines().count(), 2);

        assert!(emit(&mut ClosedPipe, &entries).unwrap().is_none());
    }

    /// A burst larger than one page must still be tailed in full: the reader
    /// walks forward through it over consecutive polls instead of jumping to the
    /// newest slice and dropping everything behind it.
    #[test]
    fn following_drains_a_burst_larger_than_one_page_without_gaps() {
        let burst: Vec<LogEntry> = (0..FOLLOW_LIMIT * 2 + 7)
            .map(|i| {
                entry(
                    &format!("log-{i}"),
                    LogTimestamp::EpochMillis(i64::from(i)),
                    "INFO",
                    &format!("line {i}"),
                )
            })
            .collect();

        let mut since: Option<String> = None;
        let mut printed = HashSet::new();
        let mut seen = Vec::new();
        let mut polls = 0;

        loop {
            let query = follow_query(since.as_deref(), None);
            let page = serve(&burst, &query);
            let poll = next_poll(page, &printed);
            if poll.fresh.is_empty() {
                break;
            }

            since = poll.fresh.last().and_then(query_timestamp);
            printed = poll.fresh.iter().map(dedupe_key).collect();
            seen.extend(poll.fresh.iter().map(|entry| entry.id.clone()));

            polls += 1;
            assert!(polls < 20, "tail failed to drain the burst");
        }

        let expected: Vec<String> = burst.iter().map(|entry| entry.id.clone()).collect();
        assert_eq!(seen, expected);
    }

    /// An entry that lands on the cursor's timestamp after the backlog must
    /// print once; the inclusive re-read must not bring back the backlog line.
    #[test]
    fn following_prints_entries_sharing_the_cursor_timestamp_once() {
        let mut served = vec![entry("a", LogTimestamp::EpochMillis(5), "INFO", "one")];
        let mut printed = HashSet::from(["a".to_string()]);
        let mut since = served.last().and_then(query_timestamp);
        served.push(entry("b", LogTimestamp::EpochMillis(5), "INFO", "two"));

        let mut seen = Vec::new();
        for _ in 0..4 {
            let page = serve(&served, &follow_query(since.as_deref(), None));
            let poll = next_poll(page, &printed);
            if poll.fresh.is_empty() {
                continue;
            }
            let keys = poll.fresh.iter().map(dedupe_key).collect();
            seen.extend(poll.fresh.iter().map(|entry| entry.id.clone()));
            advance_cursor(&mut printed, &mut since, &poll.fresh, keys);
        }

        assert_eq!(seen, ["b"]);
    }

    /// Stand-in for the host backend: return `query.limit` entries at or after
    /// `start_time`, honoring the requested direction.
    fn serve(all: &[LogEntry], query: &LogQuery<'_>) -> Vec<LogEntry> {
        let mut matching: Vec<LogEntry> = all
            .iter()
            // RFC 3339 UTC timestamps are fixed-width, so lexical order is
            // chronological order.
            .filter(|entry| match (query.start_time, query_timestamp(entry)) {
                (Some(start), Some(timestamp)) => timestamp.as_str() >= start,
                (Some(_), None) => false,
                (None, _) => true,
            })
            .cloned()
            .collect();
        if query.order == LogOrder::Newest {
            matching.reverse();
        }
        matching.truncate(query.limit as usize);
        matching
    }

    struct ClosedPipe;

    impl Write for ClosedPipe {
        fn write(&mut self, _buf: &[u8]) -> io::Result<usize> {
            Err(io::Error::from(io::ErrorKind::BrokenPipe))
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
}
