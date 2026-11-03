//! Provision the project's `schedules/` on the local dev server.
//!
//! `mda deploy` turns compiled schedules into Agent Server crons once the
//! deployment is live. The local server runs the same cron scheduler, so
//! `mda dev` runs the same reconcile against it once it answers. Startup clears
//! the build, server state included, so every `mda dev` provisions from
//! scratch; a rebuild that changes `schedules/`, or a server restart, queues
//! another reconcile, which deletes and recreates the managed crons.
//!
//! Deploy reconciles through [`langsmith::Client`](crate::langsmith::Client)
//! as the LangSmith Studio user. Local builds turn that scheme off (the dev
//! server would grant it on the header alone), so `mda dev` reconciles with
//! its own [`LocalCronClient`] as the local-dev operator instead: it presents
//! the handoff secret it staged into `.mda/build/.env` and names
//! `mda:local-dev`. Under
//! `MDA_LOCAL_DEV` the managed schedule handler lets that principal do what it
//! lets Studio do in a deploy: create crons stamped `mda_managed_by: deploy`
//! (what the compiler writes), and list or delete them.
//!
//! Not every dev server has crons: the JavaScript server that hosts TypeScript
//! projects stubs the cron routes and says so in `/info`. Then `mda dev` says
//! once that schedules are not active locally instead of listing them as if
//! they were.

use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context};
use cliclack::log;
use reqwest::blocking::Client as HttpClient;
use reqwest::Method;
use serde_json::{json, Value};

use super::dev_server_is_up;
use crate::compile::schedules::ScheduleCrons;
use crate::deploy::helpers::MDA_HANDOFF_SECRET_ENV;
use crate::deploy::{env_file, reconcile_schedules_at_url, schedules_summary, CronApi};
use crate::langsmith::{decode_json_body, ApiError, REQUEST_TIMEOUT};
use crate::telemetry::otel_logger;

/// Principal the managed runtime lets provision schedules under `mda dev`.
const LOCAL_DEV_USER: &str = "mda:local-dev";

/// Page size for cron search; a shorter page means the last page was reached.
const CRON_SEARCH_PAGE_SIZE: usize = 100;

/// How long a rebuild waits before reconciling, so a server that reloads on
/// the same change is not caught mid-restart.
const RELOAD_GRACE: Duration = Duration::from_secs(2);

/// Keeps the local server's crons in step with the compiled schedules.
pub(super) struct ScheduleSync {
    api_url: String,
    /// `.mda/build`, whose staged `.env` holds the handoff secret.
    out_dir: PathBuf,
    crons: ScheduleCrons,
    /// When a queued reconcile may run, once the server answers.
    pending: Option<Instant>,
    /// Set once the server reported it has no crons; nothing is queued after.
    unsupported: bool,
}

impl ScheduleSync {
    /// Start tracking `crons` for the server at `api_url`. A new server has no
    /// crons, so nothing is queued when the project declares none.
    pub(super) fn new(api_url: &str, out_dir: &Path, crons: ScheduleCrons) -> Self {
        let pending = (!crons.is_empty()).then(Instant::now);
        Self {
            api_url: api_url.to_string(),
            out_dir: out_dir.to_path_buf(),
            crons,
            pending,
            unsupported: false,
        }
    }

    /// Record what a rebuild compiled. A change queues a reconcile, including
    /// one that removes every schedule.
    pub(super) fn rebuilt(&mut self, crons: &ScheduleCrons) {
        if *crons == self.crons {
            return;
        }
        self.crons = crons.clone();
        if !self.unsupported {
            self.pending = Some(Instant::now() + RELOAD_GRACE);
        }
    }

    /// The server process was replaced. Reconcile again rather than rely on
    /// what the new process restored.
    pub(super) fn restarted(&mut self) {
        if !self.crons.is_empty() && !self.unsupported {
            self.pending = Some(Instant::now());
        }
    }

    /// Whether a reconcile is still due.
    pub(super) fn is_pending(&self) -> bool {
        self.pending.is_some()
    }

    /// Reconcile if one is due and the server answers. A failure never stops
    /// `mda dev`: it warns and waits for the next change or restart.
    pub(super) fn tick(&mut self) -> io::Result<()> {
        let Some(not_before) = self.pending else {
            return Ok(());
        };
        if Instant::now() < not_before || !dev_server_is_up(&self.api_url) {
            return Ok(());
        }
        self.pending = None;
        if advertised_crons(&self.api_url) == Some(false) {
            self.unsupported = true;
            if !self.crons.is_empty() {
                log::warning(UNSUPPORTED_WARNING)?;
            }
            return Ok(());
        }
        match self.reconcile() {
            Ok(removed) => {
                if let Some(summary) = schedules_summary(self.crons.len(), removed) {
                    log::success(format!("{summary} on the local dev server"))?;
                }
            }
            Err(err) => {
                otel_logger::warn("dev schedule provisioning failed", &err);
                log::warning(format!(
                    "Could not provision schedules on the local dev server: {err:#}\n\
                 Schedules will not run locally until the next change to schedules/ \
                 or a restart of `mda dev`."
                ))?;
            }
        }
        Ok(())
    }

    fn reconcile(&self) -> anyhow::Result<usize> {
        // Read on every reconcile: a restage may have replaced the secret.
        let handoff_secret = env_file::parse_env_file(&self.out_dir.join(".env"))
            .remove(MDA_HANDOFF_SECRET_ENV)
            .filter(|secret| !secret.trim().is_empty())
            .with_context(|| {
                format!("{MDA_HANDOFF_SECRET_ENV} is not staged in .mda/build/.env")
            })?;
        let client = LocalCronClient::new(handoff_secret)?;
        reconcile_schedules_at_url(&client, &self.api_url, &self.crons)
    }
}

/// Agent Server cron calls against the local server, as the local-dev
/// operator (see the module docs).
struct LocalCronClient {
    http: HttpClient,
    handoff_secret: String,
}

impl LocalCronClient {
    fn new(handoff_secret: String) -> anyhow::Result<Self> {
        let http = crate::http::client_builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .context("failed to build HTTP client")?;
        Ok(Self {
            http,
            handoff_secret,
        })
    }

    fn send(
        &self,
        method: Method,
        server_url: &str,
        path: &str,
        body: Option<Value>,
    ) -> anyhow::Result<Value> {
        let label = format!("{method} {path}");
        let mut request = self
            .http
            .request(
                method,
                format!("{}{path}", server_url.trim_end_matches('/')),
            )
            .header("x-mda-handoff-secret", &self.handoff_secret)
            .header("x-mda-user-id", LOCAL_DEV_USER);
        if let Some(body) = body {
            request = request.json(&body);
        }
        let response = request
            .send()
            .with_context(|| format!("{label} request failed"))?;
        let status = response.status();
        let text = response
            .text()
            .with_context(|| format!("failed to read the {label} response"))?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        decode_json_body(&text, path)
    }
}

impl CronApi for LocalCronClient {
    fn search_managed_crons(&self, server_url: &str) -> anyhow::Result<Value> {
        let mut all = Vec::new();
        loop {
            let body = json!({
                "limit": CRON_SEARCH_PAGE_SIZE,
                "offset": all.len(),
                "sort_by": "created_at",
                "sort_order": "asc",
            });
            let response = self.send(Method::POST, server_url, "/runs/crons/search", Some(body))?;
            let page = response
                .as_array()
                .ok_or_else(|| anyhow!("unexpected cron search response shape"))?;
            let complete = page.len() < CRON_SEARCH_PAGE_SIZE;
            all.extend(page.iter().cloned());
            if complete {
                return Ok(Value::Array(all));
            }
        }
    }

    fn create_cron(&self, server_url: &str, payload: Value) -> anyhow::Result<Value> {
        self.send(Method::POST, server_url, "/runs/crons", Some(payload))
    }

    fn create_thread_cron(
        &self,
        server_url: &str,
        thread_id: &str,
        payload: Value,
    ) -> anyhow::Result<Value> {
        let path = format!("/threads/{thread_id}/runs/crons");
        self.send(Method::POST, server_url, &path, Some(payload))
    }

    fn delete_cron(&self, server_url: &str, cron_id: &str) -> anyhow::Result<Value> {
        let path = format!("/runs/crons/{cron_id}");
        self.send(Method::DELETE, server_url, &path, None)
    }
}

/// Whether the server's `/info` says it runs crons. `None` when it does not
/// say: `http.disable_meta` turns `/info` off, and such a server is tried as
/// is.
fn advertised_crons(api_url: &str) -> Option<bool> {
    let http = crate::http::client_builder()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(5))
        .build()
        .ok()?;
    let info: Value = http
        .get(format!("{}/info", api_url.trim_end_matches('/')))
        .send()
        .ok()?
        .error_for_status()
        .ok()?
        .json()
        .ok()?;
    info.pointer("/flags/crons").and_then(Value::as_bool)
}

/// Shown once when the dev server has no crons to provision into.
const UNSUPPORTED_WARNING: &str = "Schedules are not active locally: this dev server has no \
     cron support (its /info reports crons: false). The JavaScript LangGraph dev \
     server that runs TypeScript projects does not implement crons yet. \
     Schedules run once deployed with `mda deploy`.";

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::thread;

    use serde_json::{json, Value};

    use super::*;

    /// A build directory whose staged `.env` holds a handoff secret.
    fn staged_build() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join(".env"),
            format!("{MDA_HANDOFF_SECRET_ENV}=local-handoff\n"),
        )
        .unwrap();
        dir
    }

    fn tracked(url: &str, crons: ScheduleCrons) -> (ScheduleSync, tempfile::TempDir) {
        let build = staged_build();
        (ScheduleSync::new(url, build.path(), crons), build)
    }

    fn crons(schedule: &str) -> ScheduleCrons {
        ScheduleCrons::from([(
            "daily".to_string(),
            json!({
                "graph": "agent",
                "schedule": schedule,
                "input": {"messages": [{"role": "user", "content": "Summarize"}]},
                "metadata": {"mda_managed": true, "mda_managed_by": "deploy", "mda_schedule": "daily"},
                "on_run_completed": "delete",
            }),
        )])
    }

    /// Answer each request in turn, reporting `METHOD path HTTP/1.1 <auth> body`,
    /// where `<auth>` is `handoff-secret/user-id` or `-`.
    fn serve(responses: Vec<&'static str>) -> (String, mpsc::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for body in responses {
                let (mut stream, _) = listener.accept().unwrap();
                tx.send(read_request(&mut stream)).unwrap();
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
        });
        (url, rx)
    }

    fn read_request(stream: &mut std::net::TcpStream) -> String {
        let mut data = Vec::new();
        let mut buf = [0_u8; 4096];
        loop {
            let read = stream.read(&mut buf).unwrap();
            assert!(read > 0, "client closed the connection mid-request");
            data.extend_from_slice(&buf[..read]);
            let text = String::from_utf8_lossy(&data).to_string();
            let Some((head, body)) = text.split_once("\r\n\r\n") else {
                continue;
            };
            let length = head
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
                .unwrap_or(0);
            if body.len() < length {
                continue;
            }
            let request_line = head.lines().next().unwrap_or_default();
            let header = |name: &str| {
                head.lines()
                    .find_map(|line| line.strip_prefix(&format!("{name}: ")))
                    .map(str::to_string)
            };
            assert!(header("x-auth-scheme").is_none(), "{head}");
            let auth = match (header("x-mda-handoff-secret"), header("x-mda-user-id")) {
                (Some(secret), Some(user)) => format!("{secret}/{user}"),
                _ => "-".to_string(),
            };
            return format!("{request_line} {auth} {body}");
        }
    }

    #[test]
    fn nothing_is_queued_for_a_project_without_schedules() {
        let (sync, _build) = tracked("http://127.0.0.1:9", ScheduleCrons::new());
        assert!(!sync.is_pending());
    }

    #[test]
    fn only_a_changed_rebuild_queues_a_reconcile() {
        let (mut sync, _build) = tracked("http://127.0.0.1:9", crons("0 9 * * *"));
        sync.pending = None;

        sync.rebuilt(&crons("0 9 * * *"));
        assert!(!sync.is_pending());

        sync.rebuilt(&crons("0 10 * * *"));
        assert!(sync.is_pending());

        sync.pending = None;
        sync.rebuilt(&ScheduleCrons::new());
        assert!(
            sync.is_pending(),
            "removing every schedule still reconciles"
        );
    }

    #[test]
    fn a_restart_requeues_only_when_schedules_exist() {
        let (mut sync, _build) = tracked("http://127.0.0.1:9", ScheduleCrons::new());
        sync.restarted();
        assert!(!sync.is_pending());

        let (mut sync, _build) = tracked("http://127.0.0.1:9", crons("0 9 * * *"));
        sync.pending = None;
        sync.restarted();
        assert!(sync.is_pending());
    }

    #[test]
    fn a_rebuild_waits_out_the_reload_grace() {
        let (url, requests) = serve(vec![]);
        let (mut sync, _build) = tracked(&url, ScheduleCrons::new());
        sync.rebuilt(&crons("0 9 * * *"));

        sync.tick().unwrap();

        assert!(sync.is_pending());
        assert!(requests.try_recv().is_err(), "nothing was sent yet");
    }

    #[test]
    fn a_server_that_is_down_keeps_the_reconcile_queued() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        drop(listener);
        let (mut sync, _build) = tracked(&url, crons("0 9 * * *"));

        sync.tick().unwrap();

        assert!(sync.is_pending(), "waiting on startup is not a failure");
    }

    #[test]
    fn tick_provisions_the_compiled_crons_as_the_local_dev_operator() {
        let (url, requests) = serve(vec!["{}", r#"{"flags":{"crons":true}}"#, "[]", "{}"]);
        let (mut sync, _build) = tracked(&url, crons("0 9 * * *"));

        sync.tick().unwrap();

        assert!(!sync.is_pending());
        let probe = requests.recv().unwrap();
        assert!(probe.starts_with("GET /ok "), "{probe}");
        let info = requests.recv().unwrap();
        assert!(info.starts_with("GET /info "), "{info}");
        let search = requests.recv().unwrap();
        assert!(
            search.starts_with("POST /runs/crons/search HTTP/1.1 local-handoff/mda:local-dev "),
            "{search}"
        );
        let create = requests.recv().unwrap();
        let (head, body) = create
            .split_once(" local-handoff/mda:local-dev ")
            .expect("create authenticates as the local-dev operator");
        assert_eq!(head, "POST /runs/crons HTTP/1.1");
        let payload: Value = serde_json::from_str(body).unwrap();
        assert_eq!(payload["assistant_id"], "agent");
        assert_eq!(payload["schedule"], "0 9 * * *");
        assert_eq!(payload["metadata"]["mda_managed_by"], "deploy");
    }

    #[test]
    fn a_server_without_crons_is_told_about_once_and_left_alone() {
        let (url, requests) = serve(vec![
            "{}",
            r#"{"version":"1.5.3","flags":{"assistants":true,"crons":false}}"#,
        ]);
        let (mut sync, _build) = tracked(&url, crons("0 9 * * *"));

        sync.tick().unwrap();

        assert!(!sync.is_pending());
        assert!(requests.recv().unwrap().starts_with("GET /ok "));
        assert!(requests.recv().unwrap().starts_with("GET /info "));
        assert!(
            requests.recv_timeout(Duration::from_millis(200)).is_err(),
            "no cron calls against a server without crons"
        );

        sync.rebuilt(&crons("0 10 * * *"));
        sync.restarted();
        assert!(!sync.is_pending(), "no retries once the server said no");
    }

    #[test]
    fn a_missing_handoff_secret_fails_without_calling_crons() {
        let (url, requests) = serve(vec![]);
        let build = tempfile::tempdir().unwrap();
        let sync = ScheduleSync::new(&url, build.path(), crons("0 9 * * *"));

        let err = sync.reconcile().unwrap_err();

        assert!(format!("{err:#}").contains("MDA_HANDOFF_SECRET is not staged"));
        assert!(requests.try_recv().is_err(), "nothing was sent");
    }
}
