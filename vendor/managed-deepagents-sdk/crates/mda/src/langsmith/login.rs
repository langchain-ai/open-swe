//! LangSmith sign-in via the OAuth device authorization flow (RFC 8628).
//!
//! Used when the CLI needs to act as a signed-in developer and no
//! `LANGSMITH_API_KEY` is available — today that is the interactive fallback
//! for `mda deploy` / `mda delete` / `mda logs`, and credential-backed
//! `mda dev` paths.
//!
//! LangSmith serves `/oauth/device/code` and `/oauth/token`. The CLI opens the
//! browser at the prefilled `verification_uri_complete` when LangSmith offers
//! one and the bare `verification_uri` otherwise, and prints the user code
//! either way; because that page requires a session, a developer without an
//! account signs up there and lands back on it.
//!
//! Access tokens live about five minutes, so the refresh token is cached
//! alongside them and spent on demand. After a successful sign-in the CLI mints
//! a short-lived personal access token (`lsv2_pt_…`, two hours) and caches it in
//! the machine state directory (not the project `.env`) — OAuth alone is not
//! enough for Fleet or `mda dev`. Caching is best-effort: if the state directory
//! cannot be written the developer just signs in again next time.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use cliclack::log;
use reqwest::blocking::{Client as HttpClient, Response};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::state::{create_state_dir, restrict, state_dir_from_process};

/// File holding cached OAuth sessions, keyed by LangSmith API URL.
///
/// Shared by every CLI command that signs in through this module so a session
/// from one command is reused by the others.
const STORE_FILE: &str = "builder-tokens.json";

/// File holding CLI-minted personal access tokens, keyed by LangSmith API URL.
///
/// Short-lived (see [`PAT_TTL`]) and machine-local — never written to a project
/// `.env`. User-pasted keys go to `.env`; browser-login keys live here.
const PAT_STORE_FILE: &str = "cli-api-keys.json";

/// LangSmith SDK env var for the platform API origin (regional / self-hosted).
const LANGSMITH_ENDPOINT_ENV: &str = "LANGSMITH_ENDPOINT";

/// Default LangSmith platform API (GCP US prod). OAuth and Fleet live here.
pub(crate) const DEFAULT_API_URL: &str = "https://api.smith.langchain.com";

/// Public OAuth client LangSmith registers for its command-line tools. Public
/// clients hold no secret, which is why the device flow is safe to run here.
const CLIENT_ID: &str = "langsmith-cli";

const DEVICE_GRANT: &str = "urn:ietf:params:oauth:grant-type:device_code";

/// Retire an access token slightly early, so one that would expire in flight is
/// refreshed instead of being sent and rejected.
const EXPIRY_SKEW: Duration = Duration::from_secs(30);

/// How long to wait for a developer who may have wandered off. Shorter than the
/// server's own device-code lifetime only by accident; whichever elapses first
/// ends the wait.
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

/// Lifetime for PATs minted after device login, matching `npm login` session
/// tokens. Developers who want longer sessions create their own key under
/// Settings → API Keys and paste it when prompted.
const PAT_TTL: Duration = Duration::from_secs(2 * 60 * 60);

/// CLI-minted / cache-loaded PATs exported to the process env for the current
/// run. `ensure_project_langsmith_api_key` must not treat these as shell-exported
/// durable keys on a later call (watch restage), or they get written into the
/// project `.env` and wedge after expiry.
fn session_api_keys() -> &'static Mutex<Vec<String>> {
    static KEYS: OnceLock<Mutex<Vec<String>>> = OnceLock::new();
    KEYS.get_or_init(|| Mutex::new(Vec::new()))
}

/// Record a CLI session PAT so later resolvers keep it out of the project `.env`.
pub(crate) fn note_session_api_key(key: &str) {
    let Ok(mut keys) = session_api_keys().lock() else {
        return;
    };
    if !keys.iter().any(|existing| existing == key) {
        keys.push(key.to_string());
    }
}

/// Whether `key` is a CLI session PAT for this process or the local PAT cache.
///
/// Matches expired cache entries too so a PAT that leaked into the project
/// `.env` can still be recognized and stripped rather than treated as durable.
pub(crate) fn is_cli_session_api_key(key: &str, platform_api_url: &str) -> bool {
    if session_api_keys()
        .lock()
        .map(|keys| keys.iter().any(|existing| existing == key))
        .unwrap_or(false)
    {
        return true;
    }
    Login::for_api_url(platform_api_url).is_cached_pat(key)
}

#[cfg(test)]
pub(crate) fn clear_session_api_keys_for_test() {
    if let Ok(mut keys) = session_api_keys().lock() {
        keys.clear();
    }
}

/// How the CLI authenticates to LangSmith host / platform / Context Hub APIs.
///
/// Always an API key. Device login mints a short-lived PAT into the machine
/// state directory (and the process environment for the current run) rather
/// than sending OAuth access tokens to Fleet / deploy (those reject or
/// under-authorize OAuth-as-API-key).
#[derive(Debug, Clone)]
pub(crate) enum Credential {
    /// Personal or service key (`LANGSMITH_API_KEY`).
    ApiKey(String),
}

impl Credential {
    /// Value for the `X-Api-Key` header.
    pub(crate) fn api_key(&self, _http: &HttpClient) -> Result<String> {
        match self {
            Self::ApiKey(key) => Ok(key.clone()),
        }
    }
}

/// A signed-in LangSmith developer.
#[derive(Debug, Clone)]
pub(crate) struct Login {
    store: TokenStore,
    pat_store: PatStore,
    api_url: String,
}

impl Login {
    /// Build a login handle for an explicit platform API origin.
    ///
    /// Callers that already resolved the target (deploy `--host-url`, project
    /// `LANGSMITH_ENDPOINT`, EU/staging/dev) should use this so device login
    /// hits the same installation as the rest of the CLI — not a second
    /// rediscovery pass over process env alone.
    pub(crate) fn for_api_url(api_url: impl AsRef<str>) -> Self {
        Self {
            store: TokenStore::discover(),
            pat_store: PatStore::discover(),
            api_url: normalize_platform_api_url(api_url.as_ref())
                .unwrap_or_else(|| DEFAULT_API_URL.to_string()),
        }
    }

    /// Sign in at `api_url` (reusing a cached session when possible).
    ///
    /// May open a browser and block.
    pub(crate) fn sign_in_at(api_url: impl AsRef<str>) -> Result<Self> {
        let http = crate::http::client_builder()
            .timeout(Duration::from_secs(30))
            .build()
            .context("failed to build HTTP client for LangSmith sign-in")?;
        let login = Self::for_api_url(api_url);
        let _ = login.access_token(&http)?;
        Ok(login)
    }

    /// Unexpired CLI-minted PAT for this API URL, if one is cached locally.
    pub(crate) fn cached_api_key(&self) -> Option<String> {
        self.pat_store
            .get(&self.api_url)
            .and_then(|pat| pat.unexpired().map(str::to_string))
    }

    /// Whether `key` equals the CLI-minted PAT cached for this API URL.
    ///
    /// Ignores expiry so callers can detect a session PAT that leaked into a
    /// project `.env` after the cache entry aged out.
    pub(crate) fn is_cached_pat(&self, key: &str) -> bool {
        self.pat_store
            .get(&self.api_url)
            .is_some_and(|pat| pat.key == key)
    }

    /// Mint a short-lived PAT, cache it on this machine, and export it to the
    /// process environment for the current run (and children such as `mda dev`).
    ///
    /// Does not write the project `.env` — that is reserved for keys the
    /// developer pastes deliberately. The PAT expires after [`PAT_TTL`].
    pub(crate) fn mint_and_cache_api_key(&self, http: &HttpClient) -> Result<String> {
        let access_token = self.access_token(http)?;
        let key = create_personal_access_token(http, &self.api_url, &access_token)?;
        self.pat_store.put(
            &self.api_url,
            &CachedPat {
                key: key.clone(),
                expires_at: unix_now().saturating_add(PAT_TTL.as_secs()),
            },
        );
        note_session_api_key(&key);
        std::env::set_var("LANGSMITH_API_KEY", &key);
        Ok(key)
    }

    /// A usable access token, signing the developer in if there is no live
    /// session to reuse.
    ///
    /// May open a browser and block, so call it before a long-running TUI takes
    /// over the terminal (or when the developer is already at a prompt).
    pub(crate) fn access_token(&self, http: &HttpClient) -> Result<String> {
        if let Some(session) = self.store.get(&self.api_url) {
            if let Some(token) = session.unexpired() {
                return Ok(token.to_string());
            }
            // A refresh that fails is not fatal here: the developer is present,
            // so falling through to a full sign-in is better than an error.
            if let Some(refresh) = session.refresh_token.as_deref() {
                if let Ok(refreshed) = self.spend_refresh_token(http, refresh) {
                    return Ok(refreshed);
                }
            }
        }

        let session = device_login(http, &self.api_url)?;
        let token = session.access_token.clone();
        self.store.put(&self.api_url, &session);
        Ok(token)
    }

    /// Exchange a refresh token for a fresh session and cache it.
    ///
    /// Refresh tokens rotate, so a failure leaves nothing worth keeping: the
    /// cached session is dropped rather than retried on the next run.
    fn spend_refresh_token(&self, http: &HttpClient, refresh: &str) -> Result<String> {
        match refresh_session(http, &self.api_url, refresh) {
            Ok(session) => {
                let token = session.access_token.clone();
                self.store.put(&self.api_url, &session);
                Ok(token)
            }
            Err(error) => {
                self.store.forget(&self.api_url);
                Err(error)
            }
        }
    }
}

/// Cached credentials for one LangSmith installation.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Session {
    access_token: String,
    /// Absent when the server declined to issue one, which forces a fresh
    /// sign-in once the access token lapses.
    #[serde(default)]
    refresh_token: Option<String>,
    /// Unix seconds at which `access_token` stops being accepted.
    #[serde(default)]
    expires_at: u64,
}

impl Session {
    /// The access token, or `None` once it is close enough to expiry to be
    /// worth replacing.
    fn unexpired(&self) -> Option<&str> {
        let cutoff = unix_now().saturating_add(EXPIRY_SKEW.as_secs());
        (self.expires_at > cutoff).then_some(self.access_token.as_str())
    }
}

/// What `POST /oauth/device/code` hands back.
#[derive(Debug, Deserialize)]
struct DeviceCode {
    device_code: String,
    user_code: String,
    verification_uri: String,
    /// `verification_uri` with the user code already in the query string
    /// (RFC 8628 §3.3.1). Absent on LangSmith deployments predating it.
    #[serde(default)]
    verification_uri_complete: Option<String>,
    expires_in: u64,
    interval: u64,
}

impl DeviceCode {
    /// Where to send the browser: the prefilled page when LangSmith offers one,
    /// so the developer does not retype the code, and the bare verification URI
    /// otherwise.
    fn browser_url(&self) -> &str {
        self.verification_uri_complete
            .as_deref()
            .map(str::trim)
            .filter(|uri| !uri.is_empty())
            .unwrap_or(&self.verification_uri)
    }
}

/// A successful `POST /oauth/token`, for either grant.
#[derive(Debug, Deserialize)]
struct TokenResponse {
    access_token: String,
    #[serde(default)]
    refresh_token: Option<String>,
    #[serde(default)]
    expires_in: u64,
}

impl TokenResponse {
    fn into_session(self) -> Session {
        Session {
            access_token: self.access_token,
            refresh_token: self.refresh_token,
            expires_at: unix_now().saturating_add(self.expires_in),
        }
    }
}

/// Create a PAT via `POST /api/v1/orgs/current/personal-access-tokens`.
///
/// Tries `X-Api-Key` first (same header the rest of the CLI uses for OAuth
/// access tokens), then `Authorization: Bearer` for installations that only
/// accept Bearer for this route. See
/// <https://docs.langchain.com/langsmith/smith-api/orgs/create-org-personal-access-token>.
fn create_personal_access_token(
    http: &HttpClient,
    api_url: &str,
    access_token: &str,
) -> Result<String> {
    let url = format!("{api_url}/api/v1/orgs/current/personal-access-tokens");
    let body = pat_create_body(SystemTime::now());

    let response = http
        .post(&url)
        .header("X-Api-Key", access_token)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .context("failed to create a LangSmith API key after sign-in")?;
    let status = response.status();
    let text = response
        .text()
        .context("failed to read LangSmith API key response")?;
    if status.is_success() {
        return parse_pat_key(&text);
    }

    // Some LangSmith installs authenticate browser sessions as Bearer only.
    if status.as_u16() == 401 || status.as_u16() == 403 {
        let retry = http
            .post(&url)
            .header("Authorization", format!("Bearer {access_token}"))
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .context("failed to create a LangSmith API key after sign-in")?;
        let retry_status = retry.status();
        let retry_text = retry
            .text()
            .context("failed to read LangSmith API key response")?;
        if retry_status.is_success() {
            return parse_pat_key(&retry_text);
        }
        bail!(
            "could not create a LangSmith API key after sign-in (HTTP {retry_status}): {}. \
             Create one under Settings → API Keys and paste it when prompted",
            error_detail(&retry_text)
        );
    }

    bail!(
        "could not create a LangSmith API key after sign-in (HTTP {status}): {}. \
         Create one under Settings → API Keys and paste it when prompted",
        error_detail(&text)
    );
}

/// JSON body for a CLI-minted PAT: labeled and capped at [`PAT_TTL`].
fn pat_create_body(now: SystemTime) -> Value {
    serde_json::json!({
        "description": "mda CLI",
        "expires_at": pat_expires_at(now),
    })
}

/// Absolute UTC expiry for a CLI-minted PAT (`expires_at` on the create API).
fn pat_expires_at(now: SystemTime) -> String {
    let expires = now
        .checked_add(PAT_TTL)
        .unwrap_or(now)
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format_rfc3339_utc(expires)
}

/// Format Unix seconds as `YYYY-MM-DDTHH:MM:SSZ` (OpenAPI `date-time`).
fn format_rfc3339_utc(unix_secs: u64) -> String {
    const SECS_PER_DAY: u64 = 86_400;
    const SECS_PER_HOUR: u64 = 3_600;
    const SECS_PER_MIN: u64 = 60;

    let days = unix_secs / SECS_PER_DAY;
    let rem = unix_secs % SECS_PER_DAY;
    let hour = rem / SECS_PER_HOUR;
    let min = (rem % SECS_PER_HOUR) / SECS_PER_MIN;
    let sec = rem % SECS_PER_MIN;
    let (year, month, day) = civil_from_days(days as i64);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}Z")
}

/// Civil (year, month, day) from days since the Unix epoch.
///
/// Howard Hinnant's public-domain algorithm:
/// <http://howardhinnant.github.io/date_algorithms.html#civil_from_days>
fn civil_from_days(days_since_epoch: i64) -> (i32, u32, u32) {
    let z = days_since_epoch + 719_468;
    let era = (if z >= 0 { z } else { z - 146_096 }) / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m as u32, d as u32)
}

fn parse_pat_key(body: &str) -> Result<String> {
    let parsed: Value =
        serde_json::from_str(body).context("LangSmith returned an unreadable API key response")?;
    let key = parsed
        .get("key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .context("LangSmith API key response did not include a key")?;
    Ok(key.to_string())
}

fn error_detail(body: &str) -> String {
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|value| {
            value.get("detail").map(|detail| match detail {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
        })
        .unwrap_or_else(|| body.to_string())
}

/// Hold the device authorization flow: ask for a code, send the developer to the
/// browser, and wait for them to approve.
fn device_login(http: &HttpClient, api_url: &str) -> Result<Session> {
    let device: DeviceCode = http
        .post(format!("{api_url}/oauth/device/code"))
        .form(&[("client_id", CLIENT_ID), ("resource", api_url)])
        .send()
        .and_then(Response::error_for_status)
        .context("failed to start LangSmith sign-in")?
        .json()
        .context("LangSmith returned an unreadable sign-in response")?;

    log::info(format!(
        "Sign in to LangSmith to continue:\n{}\n\nYour code: {}",
        device.verification_uri, device.user_code
    ))?;
    open_browser(device.browser_url());

    let spinner = cliclack::spinner();
    spinner.start("Waiting for you to finish signing in");
    match poll_for_token(http, api_url, &device) {
        Ok(session) => {
            spinner.stop("Signed in to LangSmith");
            Ok(session)
        }
        Err(error) => {
            spinner.error("Sign-in did not complete");
            Err(error)
        }
    }
}

/// Poll the token endpoint until the developer approves, declines, or time runs
/// out.
fn poll_for_token(http: &HttpClient, api_url: &str, device: &DeviceCode) -> Result<Session> {
    let mut interval = Duration::from_secs(device.interval.max(1));
    let budget = LOGIN_TIMEOUT.min(Duration::from_secs(device.expires_in.max(1)));
    let deadline = Instant::now() + budget;

    loop {
        std::thread::sleep(interval);
        if Instant::now() >= deadline {
            bail!("timed out waiting for LangSmith sign-in; try again");
        }

        let response = http
            .post(format!("{api_url}/oauth/token"))
            .form(&[
                ("grant_type", DEVICE_GRANT),
                ("device_code", &device.device_code),
                ("client_id", CLIENT_ID),
            ])
            .send()
            .context("failed to reach LangSmith while signing in")?;

        if response.status().is_success() {
            return Ok(response
                .json::<TokenResponse>()
                .context("LangSmith returned an unreadable sign-in token")?
                .into_session());
        }

        match oauth_error(response).as_str() {
            // Not approved yet, which is the expected answer for most of the wait.
            "authorization_pending" => {}
            "slow_down" => interval = interval.saturating_add(Duration::from_secs(5)),
            "access_denied" => bail!("LangSmith sign-in was declined"),
            "expired_token" => {
                bail!("the sign-in code expired; try again")
            }
            other => bail!("LangSmith rejected the sign-in ({other})"),
        }
    }
}

/// Trade a refresh token for a new session.
fn refresh_session(http: &HttpClient, api_url: &str, refresh: &str) -> Result<Session> {
    let response = http
        .post(format!("{api_url}/oauth/token"))
        .form(&[
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh),
            ("client_id", CLIENT_ID),
        ])
        .send()
        .context("failed to reach LangSmith to renew your sign-in")?;

    if !response.status().is_success() {
        bail!(
            "LangSmith would not renew your sign-in ({})",
            oauth_error(response)
        );
    }

    Ok(response
        .json::<TokenResponse>()
        .context("LangSmith returned an unreadable sign-in token")?
        .into_session())
}

/// The `error` field of an OAuth error response, falling back to the status when
/// the body is not the shape RFC 6749 promises.
fn oauth_error(response: Response) -> String {
    let status = response.status();
    response
        .json::<Value>()
        .ok()
        .as_ref()
        .and_then(|body| body.get("error"))
        .and_then(Value::as_str)
        .map_or_else(|| status.to_string(), str::to_string)
}

/// Send the developer to `url`, ignoring failure: the URL is printed either way,
/// and a machine without a browser is not an error.
pub(crate) fn open_browser(url: &str) {
    let (program, args): (&str, [&str; 2]) = if cfg!(target_os = "macos") {
        ("open", [url, ""])
    } else if cfg!(target_os = "windows") {
        ("rundll32", ["url.dll,FileProtocolHandler", url])
    } else {
        ("xdg-open", [url, ""])
    };
    let _ = Command::new(program)
        .args(args.iter().filter(|arg| !arg.is_empty()))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Strip trailing slashes and an optional `/api/v1` REST suffix.
///
/// OAuth lives at the platform origin; SDK env vars often include the versioned
/// REST base. Returns `None` when the value is blank after trimming.
pub(crate) fn normalize_platform_api_url(value: &str) -> Option<String> {
    let trimmed = value.trim().trim_end_matches('/');
    let origin = trimmed.strip_suffix("/api/v1").unwrap_or(trimmed);
    (!origin.is_empty()).then(|| origin.to_string())
}

/// Resolve the platform API origin for device login.
///
/// Order:
/// 1. `LANGSMITH_ENDPOINT`
/// 2. Derive from the host-backend URL (`api.host.*` → `api.smith.*`, including
///    regional / staging / dev prefixes)
/// 3. The host URL itself (local/custom single-origin installs)
/// 4. GCP US prod default
///
/// `env` should prefer process env over project `.env` when both exist.
pub(crate) fn resolve_login_api_url(
    host_url: &str,
    env: impl Fn(&str) -> Option<String>,
) -> String {
    if let Some(explicit) =
        env(LANGSMITH_ENDPOINT_ENV).and_then(|value| normalize_platform_api_url(&value))
    {
        return explicit;
    }
    if let Some(derived) = super::langsmith_api_base_url(host_url) {
        return derived;
    }
    normalize_platform_api_url(host_url).unwrap_or_else(|| DEFAULT_API_URL.to_string())
}

/// Cache of OAuth sessions, keyed by LangSmith API URL so that pointing the CLI
/// at another region does not evict the session for the default one.
#[derive(Debug, Clone)]
struct TokenStore {
    /// `None` when no writable state directory could be resolved, which turns
    /// every operation into a no-op.
    path: Option<PathBuf>,
}

impl TokenStore {
    /// Locate the OAuth session cache for this machine.
    fn discover() -> Self {
        Self {
            path: state_dir_from_process().map(|dir| dir.join(STORE_FILE)),
        }
    }

    #[cfg(test)]
    fn at(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }

    /// The session cached for `api_url`, if any.
    fn get(&self, api_url: &str) -> Option<Session> {
        let path = self.path.as_ref()?;
        read_json(path).remove(api_url)
    }

    /// Cache `session` for `api_url`, ignoring any failure to persist it.
    fn put(&self, api_url: &str, session: &Session) {
        let Some(path) = self.path.as_ref() else {
            return;
        };
        let mut sessions = read_json(path);
        sessions.insert(api_url.to_string(), session.clone());
        let _ = write_json(path, &sessions);
    }

    /// Drop the session cached for `api_url` once it is known to be unusable.
    fn forget(&self, api_url: &str) {
        let Some(path) = self.path.as_ref() else {
            return;
        };
        let mut sessions: BTreeMap<String, Session> = read_json(path);
        if sessions.remove(api_url).is_some() {
            let _ = write_json(path, &sessions);
        }
    }
}

/// CLI-minted personal access token cached for one LangSmith installation.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedPat {
    key: String,
    /// Unix seconds at which `key` stops being accepted.
    expires_at: u64,
}

impl CachedPat {
    /// The key, or `None` once it is close enough to expiry to replace.
    fn unexpired(&self) -> Option<&str> {
        let cutoff = unix_now().saturating_add(EXPIRY_SKEW.as_secs());
        (self.expires_at > cutoff).then_some(self.key.as_str())
    }
}

/// Cache of CLI-minted PATs, keyed by LangSmith API URL.
#[derive(Debug, Clone)]
struct PatStore {
    path: Option<PathBuf>,
}

impl PatStore {
    fn discover() -> Self {
        Self {
            path: state_dir_from_process().map(|dir| dir.join(PAT_STORE_FILE)),
        }
    }

    #[cfg(test)]
    fn at(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }

    fn get(&self, api_url: &str) -> Option<CachedPat> {
        let path = self.path.as_ref()?;
        read_json(path).remove(api_url)
    }

    fn put(&self, api_url: &str, pat: &CachedPat) {
        let Some(path) = self.path.as_ref() else {
            return;
        };
        let mut pats = read_json(path);
        pats.insert(api_url.to_string(), pat.clone());
        let _ = write_json(path, &pats);
    }
}

fn read_json<T>(path: &Path) -> BTreeMap<String, T>
where
    T: for<'de> Deserialize<'de>,
{
    std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn write_json<T>(path: &Path, value: &BTreeMap<String, T>) -> std::io::Result<()>
where
    T: Serialize,
{
    if let Some(parent) = path.parent() {
        create_state_dir(parent)?;
    }
    std::fs::write(path, serde_json::to_vec_pretty(value)?)?;
    restrict(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env_from<'a>(
        pairs: &'a [(&'static str, &'static str)],
    ) -> impl Fn(&str) -> Option<String> + 'a {
        move |name| {
            pairs
                .iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| (*value).to_string())
        }
    }

    fn session(access: &str, expires_in: u64) -> Session {
        Session {
            access_token: access.to_string(),
            refresh_token: Some(format!("{access}-refresh")),
            expires_at: unix_now() + expires_in,
        }
    }

    const ACTIVATE_URL: &str = "https://smith.langchain.com/activate";

    fn device_code(complete: Option<&str>) -> DeviceCode {
        DeviceCode {
            device_code: "dc".to_string(),
            user_code: "ABCD-2345".to_string(),
            verification_uri: ACTIVATE_URL.to_string(),
            verification_uri_complete: complete.map(str::to_string),
            expires_in: 900,
            interval: 5,
        }
    }

    #[test]
    fn the_browser_goes_to_the_prefilled_page_when_langsmith_offers_one() {
        let prefilled = format!("{ACTIVATE_URL}?user_code=ABCD-2345");
        assert_eq!(device_code(Some(&prefilled)).browser_url(), prefilled);
    }

    #[test]
    fn the_browser_falls_back_to_the_bare_uri_on_older_deployments() {
        assert_eq!(device_code(None).browser_url(), ACTIVATE_URL);
    }

    #[test]
    fn a_blank_prefilled_uri_is_ignored() {
        assert_eq!(device_code(Some("   ")).browser_url(), ACTIVATE_URL);
    }

    #[test]
    fn langsmith_saas_is_the_default_sign_in_target() {
        assert_eq!(
            resolve_login_api_url("https://unknown.example", env_from(&[])),
            "https://unknown.example"
        );
        assert_eq!(resolve_login_api_url("", env_from(&[])), DEFAULT_API_URL);
    }

    #[test]
    fn langsmith_endpoint_selects_the_sign_in_target() {
        assert_eq!(
            resolve_login_api_url(
                "https://api.host.langchain.com",
                env_from(&[("LANGSMITH_ENDPOINT", "https://mine.example")]),
            ),
            "https://mine.example"
        );
    }

    #[test]
    fn an_endpoint_set_to_the_rest_base_is_reduced_to_the_origin() {
        assert_eq!(
            resolve_login_api_url(
                "https://api.host.langchain.com",
                env_from(&[(
                    "LANGSMITH_ENDPOINT",
                    "https://eu.api.smith.langchain.com/api/v1/"
                )]),
            ),
            "https://eu.api.smith.langchain.com"
        );
    }

    #[test]
    fn login_api_url_derives_from_host_when_env_is_unset() {
        assert_eq!(
            resolve_login_api_url("https://eu.api.host.langchain.com", env_from(&[])),
            "https://eu.api.smith.langchain.com"
        );
        assert_eq!(
            resolve_login_api_url("https://beta.api.host.langchain.com", env_from(&[])),
            "https://beta.api.smith.langchain.com"
        );
        assert_eq!(
            resolve_login_api_url("https://dev.api.host.langchain.com", env_from(&[])),
            "https://dev.api.smith.langchain.com"
        );
    }

    #[test]
    fn login_api_url_prefers_explicit_endpoint_over_host() {
        assert_eq!(
            resolve_login_api_url(
                "https://api.host.langchain.com",
                env_from(&[("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")]),
            ),
            "https://eu.api.smith.langchain.com"
        );
    }

    #[test]
    fn for_api_url_normalizes_and_defaults_blank() {
        let login = Login {
            store: TokenStore { path: None },
            pat_store: PatStore { path: None },
            api_url: normalize_platform_api_url("https://dev.api.smith.langchain.com/api/v1/")
                .unwrap(),
        };
        assert_eq!(login.api_url, "https://dev.api.smith.langchain.com");
        assert_eq!(Login::for_api_url("").api_url, DEFAULT_API_URL.to_string());
    }

    #[test]
    fn cached_api_key_returns_only_unexpired_pats() {
        let dir = tempfile::tempdir().unwrap();
        let login = Login {
            store: TokenStore { path: None },
            pat_store: PatStore::at(dir.path().join("cli-api-keys.json")),
            api_url: "https://saas".to_string(),
        };
        login.pat_store.put(
            "https://saas",
            &CachedPat {
                key: "lsv2_pt_live".into(),
                expires_at: unix_now() + 600,
            },
        );
        assert_eq!(login.cached_api_key().as_deref(), Some("lsv2_pt_live"));

        login.pat_store.put(
            "https://saas",
            &CachedPat {
                key: "lsv2_pt_stale".into(),
                expires_at: unix_now() + EXPIRY_SKEW.as_secs() / 2,
            },
        );
        assert_eq!(login.cached_api_key(), None);
    }

    #[test]
    fn parse_pat_key_reads_the_key_field() {
        let key = parse_pat_key(
            r#"{"id":"x","short_key":"…","description":"mda CLI","key":"lsv2_pt_abc"}"#,
        )
        .unwrap();
        assert_eq!(key, "lsv2_pt_abc");
    }

    #[test]
    fn parse_pat_key_rejects_missing_key() {
        assert!(parse_pat_key(r#"{"id":"x","short_key":"…"}"#).is_err());
    }

    #[test]
    fn pat_create_body_sets_a_two_hour_expiry() {
        let now = UNIX_EPOCH + Duration::from_secs(1_700_000_000);
        let body = pat_create_body(now);
        assert_eq!(body["description"], "mda CLI");
        assert_eq!(body["expires_at"], "2023-11-15T00:13:20Z");
    }

    #[test]
    fn format_rfc3339_utc_matches_known_instants() {
        assert_eq!(format_rfc3339_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_rfc3339_utc(1_700_000_000), "2023-11-14T22:13:20Z");
        assert_eq!(
            format_rfc3339_utc(1_700_000_000 + PAT_TTL.as_secs()),
            "2023-11-15T00:13:20Z"
        );
    }

    #[test]
    fn a_live_access_token_is_reusable() {
        assert_eq!(session("live", 600).unexpired(), Some("live"));
    }

    #[test]
    fn a_token_expiring_within_the_skew_is_already_spent() {
        assert_eq!(
            session("stale", EXPIRY_SKEW.as_secs() / 2).unexpired(),
            None
        );
    }

    #[test]
    fn sessions_round_trip_per_installation() {
        let dir = tempfile::tempdir().unwrap();
        let store = TokenStore::at(dir.path().join("nested/tokens.json"));

        store.put("https://saas", &session("token-a", 600));
        store.put("https://eu", &session("token-b", 600));

        assert_eq!(
            store.get("https://saas").unwrap().access_token,
            "token-a".to_string()
        );
        assert_eq!(
            store.get("https://eu").unwrap().refresh_token,
            Some("token-b-refresh".to_string())
        );
        assert!(store.get("https://unknown").is_none());
    }

    #[test]
    fn a_rejected_session_can_be_forgotten_without_disturbing_the_others() {
        let dir = tempfile::tempdir().unwrap();
        let store = TokenStore::at(dir.path().join("tokens.json"));
        store.put("https://saas", &session("token-a", 600));
        store.put("https://eu", &session("token-b", 600));

        store.forget("https://saas");

        assert!(store.get("https://saas").is_none());
        assert!(store.get("https://eu").is_some());
    }

    #[cfg(unix)]
    #[test]
    fn the_cache_is_not_world_readable() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tokens.json");
        TokenStore::at(path.clone()).put("https://saas", &session("token", 600));

        let mode = std::fs::metadata(&path).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0, "group and other bits must be clear");
    }

    #[test]
    fn a_cache_written_by_an_older_cli_is_treated_as_empty_rather_than_fatal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tokens.json");
        // Guest tokens were stored as bare strings before sign-in existed.
        std::fs::write(&path, r#"{"https://saas":"guest-token"}"#).unwrap();
        let store = TokenStore::at(path);

        assert!(store.get("https://saas").is_none());
        store.put("https://saas", &session("token", 600));
        assert_eq!(
            store.get("https://saas").unwrap().access_token,
            "token".to_string()
        );
    }

    #[test]
    fn without_a_state_directory_every_operation_is_a_no_op() {
        let store = TokenStore { path: None };

        store.put("https://saas", &session("token", 600));
        store.forget("https://saas");

        assert!(store.get("https://saas").is_none());
    }

    #[test]
    fn credential_api_key_returns_the_static_key() {
        let http = HttpClient::new();
        let credential = Credential::ApiKey("lsv2_pt_test".into());
        assert_eq!(credential.api_key(&http).unwrap(), "lsv2_pt_test");
    }
}
