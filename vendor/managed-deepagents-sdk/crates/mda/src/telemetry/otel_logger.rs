//! The CLI's OTel logger: records sent to the LangSmith CLI log collection
//! endpoint as OTLP/HTTP JSON.
//!
//! [`OtelLogger::install`] makes it the process's logger. From then on
//! records are made with the current [`ReportableFacts`] attached and sent in
//! batches in the background, to wherever [`authenticate`] last pointed.
//! Records made before a credential arrives are held until one does.
//! [`OtelLogger::shutdown`] sends what is left, or drops it when no credential
//! ever arrived. This is the only module that uses `tracing` directly.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, PoisonError};
use std::time::Duration;

use opentelemetry::logs::{AnyValue, LogRecord as _};
use opentelemetry::{InstrumentationScope, SpanId, TraceId};
use opentelemetry_appender_tracing::layer::OpenTelemetryTracingBridge;
use opentelemetry_otlp::{Protocol, RetryPolicy, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::error::OTelSdkResult;
use opentelemetry_sdk::logs::{
    BatchConfigBuilder, BatchLogProcessor, LogBatch, LogExporter, LogProcessor, SdkLogRecord,
    SdkLoggerProvider,
};
use opentelemetry_sdk::Resource;
use serde::Serialize;
use serde_json::Value;
use tracing::level_filters::LevelFilter;
use tracing_subscriber::filter::Targets;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::Layer;
use uuid::Uuid;

use crate::command::CommandCompletion;
use crate::langsmith::Client;

use super::event::CommandCompleted;
use super::facts::{facts, ReportableFacts};

const LOGS_PATH: &str = "/v1/platform/mda/otel/v1/logs";
const SERVICE_NAME: &str = "mda-cli";
/// Bounds one export, retries included; the same as the Node and Python
/// runtimes. It matters only while a long-running command (`mda dev`,
/// `mda logs --follow`) sends batches in the background. At the end of a
/// command, what is left is sent best effort within exit's 1s window
/// (`telemetry::exit`), however long this is.
const EXPORT_TIMEOUT: Duration = Duration::from_secs(10);
/// The collection endpoint accepts at most this many records per request.
const MAX_BATCH_RECORDS: usize = 200;
/// How long a partial batch waits for more records.
const BATCH_DELAY: Duration = Duration::from_secs(1);

/// Where this process's records go, shared with [`authenticate`].
static EXPORTER: OnceLock<DeferredExporter<opentelemetry_otlp::LogExporter>> = OnceLock::new();
/// This command's span. Every OTel record of the command carries it under the
/// run's trace: stage records, errors, warnings and the completion record.
/// PostHog's `command_completed` event has no span.
static SPAN_ID: OnceLock<SpanId> = OnceLock::new();

/// The installed logger. Hold it until exit, then [`Self::shutdown`] it.
pub struct OtelLogger {
    provider: SdkLoggerProvider,
}

impl OtelLogger {
    /// Make this the process's logger. `None` when another logger is already
    /// installed.
    pub fn install() -> Option<Self> {
        let exporter = DeferredExporter::default();
        let provider = provider(FactsProcessor {
            inner: batch_processor(exporter.clone()),
            read: facts,
        });
        tracing::subscriber::set_global_default(subscriber(&provider)).ok()?;
        EXPORTER.set(exporter).ok()?;
        Some(Self { provider })
    }

    /// Send the run's records and stop. Failures never affect the command.
    pub fn shutdown(self) {
        let _ = self.provider.shutdown();
    }
}

/// Send this run's records to the LangSmith instance `client` points at,
/// authenticated as `client`. A later call replaces an earlier one, so a
/// sign-in partway through a command still gets its records sent. Ignored
/// when the logger is not installed, or when the key would cross the network
/// in cleartext.
pub fn authenticate(client: &Client) {
    let Some(deferred) = EXPORTER.get() else {
        return;
    };
    if !client.sends_key_securely() {
        return;
    }
    let Ok(headers) = client.auth_headers() else {
        return;
    };
    if let Some(exporter) = build_exporter(logs_url(client.platform_base()), headers) {
        deferred.send_with(exporter);
    }
}

/// Record an error where it stops propagating. `exception.message` is the
/// top message; `exception.stacktrace` is the whole chain, each cause under
/// `Caused by`, so a failed child's stderr comes last.
pub fn error(err: &anyhow::Error) {
    let stacktrace = elide_middle(chain_report(err), MAX_STACKTRACE_BYTES);
    tracing::error!(exception.stacktrace = %stacktrace, exception.message = %err, "{err}");
}

/// Record a failure the command recovers from, in the same shape as
/// [`error`]. `message` says what was being attempted.
pub fn warn(message: &str, err: &anyhow::Error) {
    let stacktrace = elide_middle(chain_report(err), MAX_STACKTRACE_BYTES);
    tracing::warn!(exception.stacktrace = %stacktrace, exception.message = %err, "{message}");
}

/// Record a panic, in the same shape as [`error`] without causes.
pub fn panic(message: &str) {
    tracing::error!(exception.message = %message, "{message}");
}

/// The collection endpoint will keep up to 32KB of a value (MDAC-191); cutting
/// the middle here keeps both ends instead of whatever it would keep. Also how
/// much of a crashed child's stderr is worth keeping for its error.
pub const MAX_STACKTRACE_BYTES: usize = 32 << 10;
/// Kept from the start of a stacktrace that is too long: our messages and the
/// command that failed. The rest of the budget keeps its end, where a
/// traceback or an installer names the cause.
const STACKTRACE_HEAD_BYTES: usize = 1 << 10;
/// Room for the omission marker.
const ELISION_MARKER_BYTES: usize = 48;

/// anyhow's `{:?}` layout, without the backtrace it may append, so the budget
/// goes to the causes.
fn chain_report(err: &anyhow::Error) -> String {
    let mut report = err.to_string();
    let causes: Vec<String> = err.chain().skip(1).map(ToString::to_string).collect();
    if causes.is_empty() {
        return report;
    }
    report.push_str("\n\nCaused by:");
    for (index, cause) in causes.iter().enumerate() {
        let prefix = if causes.len() == 1 {
            "    ".to_string()
        } else {
            format!("    {index}: ")
        };
        let continuation = " ".repeat(prefix.len());
        for (line_number, line) in cause.lines().enumerate() {
            report.push('\n');
            report.push_str(if line_number == 0 {
                &prefix
            } else {
                &continuation
            });
            report.push_str(line);
        }
    }
    report
}

/// `text` within `max` bytes: its start and end, with how much was cut from
/// the middle in between.
fn elide_middle(text: String, max: usize) -> String {
    if text.len() <= max {
        return text;
    }
    let head_end = floor_boundary(&text, STACKTRACE_HEAD_BYTES);
    let tail_len = max - STACKTRACE_HEAD_BYTES - ELISION_MARKER_BYTES;
    let tail_start = ceil_boundary(&text, text.len() - tail_len);
    format!(
        "{}\n… {} bytes omitted …\n{}",
        &text[..head_end],
        tail_start - head_end,
        &text[tail_start..]
    )
}

fn floor_boundary(text: &str, mut index: usize) -> usize {
    while !text.is_char_boundary(index) {
        index -= 1;
    }
    index
}

fn ceil_boundary(text: &str, mut index: usize) -> usize {
    while !text.is_char_boundary(index) {
        index += 1;
    }
    index
}

/// Record how the command ended, with the fields PostHog's `command_completed`
/// carries. Recorded even when there is no installation id for that event.
pub fn record_completion(completion: CommandCompletion) {
    // Destructured so a field added to `command_completed` fails to compile
    // here instead of going missing from the record.
    let CommandCompleted {
        outcome,
        error_code,
        error_category,
    } = completion.into();
    tracing::info!(
        outcome = serialized_name(&outcome),
        error_code = error_code.as_ref().and_then(serialized_name),
        error_category = error_category.as_ref().and_then(serialized_name),
        "command completed"
    );
}

/// The name a unit enum variant serializes to, as PostHog sees it.
pub(super) fn serialized_name(value: &impl Serialize) -> Option<String> {
    serde_json::to_value(value)
        .ok()?
        .as_str()
        .map(str::to_string)
}

/// The exporter [`authenticate`] supplied last, behind the SDK's batch
/// processor. Until one arrives, batches are held (up to one request's worth),
/// so records made before a sign-in partway through a command still go out.
#[derive(Debug)]
struct DeferredExporter<E>(Arc<Mutex<Deferred<E>>>);

#[derive(Debug)]
struct Deferred<E> {
    exporter: Option<Arc<E>>,
    held: Vec<(SdkLogRecord, InstrumentationScope)>,
    resource: Option<Resource>,
}

impl<E> Default for DeferredExporter<E> {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(Deferred {
            exporter: None,
            held: Vec::new(),
            resource: None,
        })))
    }
}

impl<E> Clone for DeferredExporter<E> {
    fn clone(&self) -> Self {
        Self(Arc::clone(&self.0))
    }
}

impl<E: LogExporter> DeferredExporter<E> {
    fn send_with(&self, mut exporter: E) {
        let mut deferred = self.lock();
        if let Some(resource) = &deferred.resource {
            exporter.set_resource(resource);
        }
        deferred.exporter = Some(Arc::new(exporter));
    }
}

impl<E> DeferredExporter<E> {
    fn lock(&self) -> MutexGuard<'_, Deferred<E>> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

impl<E: LogExporter + std::fmt::Debug + 'static> LogExporter for DeferredExporter<E> {
    async fn export(&self, batch: LogBatch<'_>) -> OTelSdkResult {
        let (exporter, held) = {
            let mut deferred = self.lock();
            let Some(exporter) = deferred.exporter.clone() else {
                let room = MAX_BATCH_RECORDS.saturating_sub(deferred.held.len());
                let records = batch.iter().take(room);
                deferred
                    .held
                    .extend(records.map(|(record, scope)| (record.clone(), scope.clone())));
                return Ok(());
            };
            (exporter, std::mem::take(&mut deferred.held))
        };
        if !held.is_empty() {
            let refs: Vec<_> = held.iter().map(|(record, scope)| (record, scope)).collect();
            let _ = exporter.export(LogBatch::new(&refs)).await;
        }
        exporter.export(batch).await
    }

    fn shutdown_with_timeout(&self, timeout: Duration) -> OTelSdkResult {
        let exporter = self.lock().exporter.clone();
        exporter.map_or(Ok(()), |exporter| exporter.shutdown_with_timeout(timeout))
    }

    fn set_resource(&mut self, resource: &Resource) {
        self.lock().resource = Some(resource.clone());
    }
}

/// Sends a batch once it is full or [`BATCH_DELAY`] after its first record.
fn batch_processor<E: LogExporter + 'static>(exporter: E) -> BatchLogProcessor {
    BatchLogProcessor::builder(exporter)
        .with_batch_config(
            BatchConfigBuilder::default()
                .with_max_export_batch_size(MAX_BATCH_RECORDS)
                .with_scheduled_delay(BATCH_DELAY)
                .build(),
        )
        .build()
}

fn provider(processor: impl LogProcessor + 'static) -> SdkLoggerProvider {
    SdkLoggerProvider::builder()
        .with_resource(
            Resource::builder_empty()
                .with_service_name(SERVICE_NAME)
                .with_attribute(opentelemetry::KeyValue::new(
                    "service.version",
                    crate::version::package_version(),
                ))
                .build(),
        )
        .with_log_processor(processor)
        .build()
}

fn build_exporter(
    url: String,
    headers: Vec<(&str, String)>,
) -> Option<opentelemetry_otlp::LogExporter> {
    let http = crate::http::client_builder()
        .timeout(EXPORT_TIMEOUT)
        .build()
        .ok()?;
    let headers: HashMap<String, String> = headers
        .into_iter()
        .map(|(name, value)| (name.to_string(), value))
        .collect();
    opentelemetry_otlp::LogExporter::builder()
        .with_http()
        .with_http_client(http)
        .with_protocol(Protocol::HttpJson)
        .with_endpoint(url)
        .with_headers(headers)
        .with_timeout(EXPORT_TIMEOUT)
        // Retries 429, 5xx and network failures, honoring `Retry-After`.
        .with_retry_policy(RetryPolicy::recommended())
        .build()
        .ok()
}

fn logs_url(platform_url: &str) -> String {
    format!("{}{LOGS_PATH}", platform_url.trim_end_matches('/'))
}

/// Only the CLI's own records at INFO and above; dependencies stay out.
fn subscriber(provider: &SdkLoggerProvider) -> impl tracing::Subscriber + Send + Sync {
    let filter = Targets::new().with_target(env!("CARGO_CRATE_NAME"), LevelFilter::INFO);
    tracing_subscriber::registry()
        .with(OpenTelemetryTracingBridge::new(provider).with_filter(filter))
}

/// OTel semantic-convention attribute for a log record's unique id, so copies
/// of a batch sent twice can be told apart from distinct records.
const RECORD_UID_KEY: &str = "log.record.uid";

/// Attaches the facts current when each record is made, so a fact learned
/// mid-command appears on every later record. The collection endpoint nests
/// these attributes under `mda`, so they keep their facts names. The record's
/// trace id is the invocation id, so one run's records share a trace and will
/// join its spans once the CLI emits them.
#[derive(Debug)]
struct FactsProcessor<P> {
    inner: P,
    /// [`facts`] in production; tests pass their own so they never share it.
    read: fn() -> Option<ReportableFacts>,
}

impl<P: LogProcessor> LogProcessor for FactsProcessor<P> {
    fn emit(&self, record: &mut SdkLogRecord, scope: &InstrumentationScope) {
        // Set once, before batching, so a retried batch keeps each record's id.
        record.add_attribute(RECORD_UID_KEY, Uuid::new_v4().to_string());
        if let Some(facts) = (self.read)() {
            if let Ok(invocation_id) = Uuid::parse_str(&facts.run.invocation_id) {
                let trace_id = TraceId::from_bytes(invocation_id.into_bytes());
                record.set_trace_context(trace_id, command_span_id(), None);
            }
            if let Some(current) = facts.stage {
                record.add_attribute("stage", current.stage.as_str());
            }
            if let Ok(Value::Object(fields)) = serde_json::to_value(facts) {
                for (name, value) in fields {
                    if let Some(value) = attribute_value(value) {
                        record.add_attribute(name, value);
                    }
                }
            }
        }
        self.inner.emit(record, scope);
    }

    fn force_flush(&self) -> OTelSdkResult {
        self.inner.force_flush()
    }

    fn shutdown_with_timeout(&self, timeout: Duration) -> OTelSdkResult {
        self.inner.shutdown_with_timeout(timeout)
    }

    fn set_resource(&mut self, resource: &Resource) {
        self.inner.set_resource(resource);
    }
}

/// A random id for this command's span, the same on every record.
fn command_span_id() -> SpanId {
    *SPAN_ID.get_or_init(|| {
        let bytes = Uuid::new_v4().into_bytes();
        SpanId::from_bytes(bytes[..8].try_into().unwrap_or_default())
    })
}

/// Unknown facts (`null`) are left off the record rather than sent empty.
fn attribute_value(value: Value) -> Option<AnyValue> {
    Some(match value {
        Value::Null => return None,
        Value::String(text) => AnyValue::from(text),
        Value::Bool(flag) => AnyValue::from(flag),
        Value::Array(items) => AnyValue::ListAny(Box::new(
            items.into_iter().filter_map(attribute_value).collect(),
        )),
        other => AnyValue::from(other.to_string()),
    })
}

#[cfg(test)]
pub(super) mod tests {
    use std::sync::Mutex;
    use std::time::Instant;

    use opentelemetry::logs::Severity;
    use opentelemetry_sdk::logs::in_memory_exporter::LogDataWithResource;
    use opentelemetry_sdk::logs::{InMemoryLogExporter, LogBatch, SimpleLogProcessor};

    use super::*;
    use crate::error::ErrorCategory;
    use crate::telemetry::event::fixture_facts;

    /// Facts for one test, so tests running in parallel never share them.
    static TEST_FACTS: Mutex<Option<ReportableFacts>> = Mutex::new(None);

    fn test_facts() -> Option<ReportableFacts> {
        TEST_FACTS.lock().unwrap().clone()
    }

    fn no_facts() -> Option<ReportableFacts> {
        None
    }

    pub(in crate::telemetry) fn capture(
        read: fn() -> Option<ReportableFacts>,
        emit: impl FnOnce(),
    ) -> Vec<LogDataWithResource> {
        let exporter = InMemoryLogExporter::default();
        let provider = provider(FactsProcessor {
            inner: SimpleLogProcessor::new(exporter.clone()),
            read,
        });
        tracing::subscriber::with_default(subscriber(&provider), emit);
        provider.force_flush().unwrap();
        exporter.get_emitted_logs().unwrap()
    }

    pub(in crate::telemetry) fn attribute(
        log: &LogDataWithResource,
        key: &str,
    ) -> Option<AnyValue> {
        log.record
            .attributes_iter()
            .find(|(name, _)| name.as_str() == key)
            .map(|(_, value)| value.clone())
    }

    pub(in crate::telemetry) fn text(value: &str) -> Option<AnyValue> {
        Some(AnyValue::from(value.to_string()))
    }

    /// Keeps the bodies it is sent, even after shutdown.
    #[derive(Clone, Debug, Default)]
    struct Captured(Arc<Mutex<Vec<Option<AnyValue>>>>);

    impl LogExporter for Captured {
        async fn export(&self, batch: LogBatch<'_>) -> OTelSdkResult {
            let bodies = batch.iter().map(|(record, _)| record.body().cloned());
            self.0.lock().unwrap().extend(bodies);
            Ok(())
        }
    }

    impl Captured {
        fn bodies(&self) -> Vec<Option<AnyValue>> {
            self.0.lock().unwrap().clone()
        }
    }

    // The one test that writes `TEST_FACTS`, so nothing changes it mid-run.
    #[test]
    fn records_carry_the_facts_current_when_they_were_made() {
        *TEST_FACTS.lock().unwrap() = Some(ReportableFacts {
            stage: Some(super::super::stage::CurrentStage {
                stage: super::super::stage::Stage::Compile,
                started: Instant::now(),
            }),
            ..fixture_facts()
        });

        let logs = capture(test_facts, || {
            tracing::info!("before");
            TEST_FACTS
                .lock()
                .unwrap()
                .as_mut()
                .unwrap()
                .deployment
                .deployment_id = Some("deployment-1".to_string());
            tracing::info!("after");
        });

        assert_eq!(logs.len(), 2);
        for log in &logs {
            assert_eq!(
                attribute(log, "invocation_id"),
                text("11111111-1111-4111-8111-111111111111")
            );
            assert_eq!(attribute(log, "command_path"), text("channel init slack"));
            assert_eq!(attribute(log, "user_id"), None);
            assert_eq!(attribute(log, "stage"), text("compile"));
            assert_eq!(
                log.record.trace_context().map(|context| context.span_id),
                Some(command_span_id())
            );
            assert_ne!(command_span_id(), SpanId::INVALID);
            assert_eq!(
                log.record.trace_context().map(|context| context.trace_id),
                Some(TraceId::from_hex("11111111111141118111111111111111").unwrap())
            );
        }
        assert_eq!(attribute(&logs[0], "deployment_id"), None);
        assert_eq!(attribute(&logs[1], "deployment_id"), text("deployment-1"));
    }

    /// The stacktrace as text, ignoring a backtrace the test environment may add.
    fn stacktrace(log: &LogDataWithResource) -> String {
        match attribute(log, "exception.stacktrace") {
            Some(AnyValue::String(text)) => text.to_string(),
            other => panic!("expected a stacktrace, got {other:?}"),
        }
    }

    #[test]
    fn an_error_reports_its_message_and_its_causes() {
        let err = anyhow::anyhow!("inner").context("outer");

        let logs = capture(no_facts, || error(&err));

        assert_eq!(logs[0].record.severity_number(), Some(Severity::Error));
        assert_eq!(attribute(&logs[0], "exception.message"), text("outer"));
        assert!(stacktrace(&logs[0]).starts_with("outer\n\nCaused by:\n    inner"));
    }

    #[cfg(unix)]
    #[test]
    fn a_failed_childs_stderr_is_the_last_cause() {
        use crate::error::child_failed;

        let output = std::process::Command::new("sh")
            .args(["-c", "echo 'ImportError: x' >&2; exit 1"])
            .output()
            .unwrap();
        let err = child_failed(&output, "emit failed").context("build failed");

        let logs = capture(no_facts, || error(&err));

        assert_eq!(
            attribute(&logs[0], "exception.message"),
            text("build failed")
        );
        assert!(stacktrace(&logs[0])
            .starts_with("build failed\n\nCaused by:\n    0: emit failed\n    1: ImportError: x"));
    }

    #[test]
    fn a_warning_reports_what_failed_and_its_causes() {
        let err = anyhow::anyhow!("inner").context("outer");

        let logs = capture(no_facts, || warn("status check failed", &err));

        assert_eq!(logs[0].record.severity_number(), Some(Severity::Warn));
        assert_eq!(logs[0].record.body().cloned(), text("status check failed"));
        assert_eq!(attribute(&logs[0], "exception.message"), text("outer"));
        assert!(stacktrace(&logs[0]).starts_with("outer\n\nCaused by:\n    inner"));
    }

    #[test]
    fn a_panic_is_an_error_record() {
        let logs = capture(no_facts, || panic("panicked at src/main.rs:1:1: boom"));

        assert_eq!(logs[0].record.severity_number(), Some(Severity::Error));
        assert_eq!(
            attribute(&logs[0], "exception.message"),
            text("panicked at src/main.rs:1:1: boom")
        );
    }

    #[test]
    fn a_long_stacktrace_keeps_both_ends() {
        let stderr = format!(
            "npm ERR! start\n{}\nImportError: the cause",
            "é".repeat(20_000)
        );
        let err = anyhow::anyhow!(stderr).context("install failed");

        let logs = capture(no_facts, || error(&err));
        let stacktrace = stacktrace(&logs[0]);

        assert!(
            stacktrace.len() <= MAX_STACKTRACE_BYTES,
            "{}",
            stacktrace.len()
        );
        assert!(stacktrace.starts_with("install failed\n\nCaused by:\n    npm ERR! start"));
        assert!(stacktrace.contains(" bytes omitted …\n"));
        assert!(stacktrace.ends_with("ImportError: the cause"));
    }

    #[test]
    fn a_short_stacktrace_is_left_whole() {
        let text = "outer\n\nCaused by:\n    inner".to_string();

        assert_eq!(elide_middle(text.clone(), MAX_STACKTRACE_BYTES), text);
    }

    #[test]
    fn the_completion_record_matches_command_completed() {
        let logs = capture(no_facts, || {
            record_completion(CommandCompletion::Succeeded);
            record_completion(CommandCompletion::Failed(ErrorCategory::Deployment));
        });

        assert_eq!(attribute(&logs[0], "outcome"), text("success"));
        assert_eq!(attribute(&logs[0], "error_code"), None);
        assert_eq!(attribute(&logs[1], "outcome"), text("failure"));
        assert_eq!(attribute(&logs[1], "error_code"), text("unclassified"));
        assert_eq!(attribute(&logs[1], "error_category"), text("deployment"));
    }

    fn facts_without_an_installation_id() -> Option<ReportableFacts> {
        let mut facts = fixture_facts();
        facts.run.install_id = None;
        Some(facts)
    }

    #[test]
    fn a_run_without_an_installation_id_still_records_its_completion() {
        let logs = capture(facts_without_an_installation_id, || {
            record_completion(CommandCompletion::Succeeded);
        });

        assert_eq!(logs.len(), 1);
        assert_eq!(attribute(&logs[0], "outcome"), text("success"));
        assert_eq!(attribute(&logs[0], "install_id"), None);
        assert_eq!(
            attribute(&logs[0], "invocation_id"),
            text("11111111-1111-4111-8111-111111111111")
        );
    }

    #[test]
    fn records_made_before_a_credential_are_sent_once_one_arrives() {
        let captured = Captured::default();
        let deferred = DeferredExporter::default();
        let provider = provider(batch_processor(deferred.clone()));

        tracing::subscriber::with_default(subscriber(&provider), || tracing::info!("before"));
        provider.force_flush().unwrap();
        deferred.send_with(captured.clone());
        tracing::subscriber::with_default(subscriber(&provider), || tracing::info!("after"));
        provider.shutdown().unwrap();

        assert_eq!(captured.bodies(), [text("before"), text("after")]);
    }

    #[test]
    fn records_are_sent_while_the_command_runs() {
        let captured = Captured::default();
        let deferred = DeferredExporter::default();
        deferred.send_with(captured.clone());
        let provider = provider(batch_processor(deferred));
        let started = Instant::now();

        tracing::subscriber::with_default(subscriber(&provider), || tracing::info!("now"));
        while captured.bodies().is_empty() && started.elapsed() < BATCH_DELAY * 5 {
            std::thread::sleep(Duration::from_millis(50));
        }

        assert_eq!(captured.bodies(), [text("now")]);
        let _ = provider.shutdown();
    }

    #[test]
    fn every_record_gets_its_own_uid() {
        let logs = capture(no_facts, || {
            tracing::info!("first");
            tracing::info!("second");
        });

        let uids: Vec<_> = logs
            .iter()
            .map(|log| match attribute(log, RECORD_UID_KEY) {
                Some(AnyValue::String(uid)) => Uuid::parse_str(uid.as_str()).unwrap(),
                other => panic!("no uid: {other:?}"),
            })
            .collect();
        assert_eq!(uids.len(), 2);
        assert_ne!(uids[0], uids[1]);
    }

    #[test]
    fn debug_records_are_not_exported() {
        let logs = capture(no_facts, || tracing::debug!("noise"));

        assert!(logs.is_empty());
    }

    #[test]
    fn the_logs_url_joins_the_platform_root() {
        assert_eq!(
            logs_url("https://api.example.com/"),
            "https://api.example.com/v1/platform/mda/otel/v1/logs"
        );
    }
}
