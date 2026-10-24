//! `mda connections` — manage workspace-scoped connections (opaque secrets and OAuth slots).
//!
//! A value only travels one way. Nothing here reads a secret back.

use std::collections::BTreeMap;
use std::io::{self, IsTerminal, Write};
use std::net::IpAddr;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use reqwest::StatusCode;
use serde_json::{Map, Value};

use crate::agent_config;
use crate::cli_output::{continue_sequence, show_sequence, SequenceItem};
use crate::command::CommandCompletion;
use crate::compile;
use crate::deploy::helpers::{
    deployment_managed_deep_agent_marker, find_deployment_by_name, resolve_langsmith_api_bases,
    resolve_platform_api_url, REMOVED_WORKSPACE_ID_ENVS, WORKSPACE_ID_ENV,
};
use crate::deploy::{env_file, resolve_api_key};
use crate::error::{categorized, category_of, is_cancellation, ErrorCategory};
use crate::install::Installation;
use crate::interrupt::Interrupt;
use crate::langsmith::{
    self, resolve_preferred_env, ApiError, ConnectionOwnerPolicy, ConnectionSummary,
    CreateOAuthConnection,
};
use crate::project;
use crate::secret_input::{
    read_piped_stdin_secret, resolve_value, value_source_is_explicit, ResolvedValue, ValueSource,
};

pub mod catalog;

pub struct ConnectionTarget {
    pub path: PathBuf,
    pub workspace_id: Option<String>,
    pub host_url: Option<String>,
}

pub struct CreateOptions {
    pub target: ConnectionTarget,
    pub slug: String,
    pub value: ValueSource,
    pub authorize: bool,
    pub oauth: Option<OAuthCreateOptions>,
    pub mcp: Option<String>,
}

/// OAuth flags for `mda connections create`.
#[derive(Debug, Clone, Default)]
pub struct OAuthCreateOptions {
    pub service: Option<String>,
    pub grant_type: Option<String>,
    pub client_id: Option<String>,
    pub scopes: Vec<String>,
    pub allowed_scopes: Vec<String>,
    pub authorization_params: Vec<String>,
    pub token_params: Vec<String>,
    pub token_request_headers: Vec<String>,
    pub auth_method: Option<String>,
    pub authorize_url: Option<String>,
    pub token_url: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
enum OAuthGrantType {
    #[default]
    AuthorizationCode,
    ClientCredentials,
}

impl OAuthGrantType {
    fn as_str(self) -> &'static str {
        match self {
            Self::AuthorizationCode => "authorization_code",
            Self::ClientCredentials => "client_credentials",
        }
    }
}

pub fn oauth_create_options(options: OAuthCreateOptions) -> Option<OAuthCreateOptions> {
    oauth_create_requested(&options).then_some(options)
}

pub struct ListOptions {
    pub target: ConnectionTarget,
    pub json: bool,
}

pub struct GetOptions {
    pub target: ConnectionTarget,
    pub slug: String,
    pub json: bool,
}

pub struct DeleteOptions {
    pub target: ConnectionTarget,
    pub slug: String,
    pub yes: bool,
}

struct Session {
    client: langsmith::Client,
}

fn agent_created_items(slug: &str, source: &str) -> [SequenceItem; 1] {
    let mut details = vec![slug.to_string()];
    if !source.is_empty() {
        details.push(format!("Source: {source}"));
    }
    [SequenceItem::section("Agent connection created", details)]
}

fn oauth_redirect_uri_items(redirect_uri: &str, register_at: Option<&str>) -> [SequenceItem; 1] {
    let mut details = vec![redirect_uri.to_string()];
    if let Some(url) = register_at {
        details.push(String::new());
        details.push(format!("Register an app at {url}"));
    }
    [SequenceItem::section("Redirect URI", details)]
}

/// The registered scopes are the only thing that decides whether the provider's
/// consent screen will accept the authorization request, so they are reported
/// alongside the redirect URI. A manual registration that named none is almost
/// always a mistake: a catalog service supplies its own, and a provider that
/// genuinely takes no scope is rare enough to be worth stating out loud.
fn oauth_created_items(
    slug: &str,
    redirect_uri: &str,
    scopes: &[String],
    service: Option<&str>,
) -> Vec<SequenceItem> {
    let summary = if scopes.is_empty() {
        "Scopes: none".to_string()
    } else {
        format!("Scopes: {}", scopes.join(" "))
    };
    let mut items = vec![SequenceItem::section(
        "OAuth connection created",
        [slug, &format!("Redirect URI: {redirect_uri}"), &summary],
    )];
    if scopes.is_empty() && service.is_none() {
        items.push(SequenceItem::section(
            "No scopes registered",
            [
                "Connect will send an authorization request without a scope parameter.",
                "Most providers reject that. Re-create with --scope, or with --oauth \
                 <service> when the catalog covers this provider.",
            ],
        ));
    }
    items
}

fn mcp_oauth_created_items(slug: &str, url: &str) -> [SequenceItem; 1] {
    [SequenceItem::section(
        "Automatically discovered and created MCP connection",
        [
            format!("connection:   {slug}"),
            format!("url:          {url}"),
        ],
    )]
}

fn user_slot_created_items(slug: &str) -> [SequenceItem; 1] {
    [SequenceItem::section(
        "User connection slot created",
        [
            slug.to_string(),
            "Callers grant values at runtime".to_string(),
        ],
    )]
}

fn authorization_started_item(slug: &str, verification_url: &str) -> SequenceItem {
    SequenceItem::section(
        format!("Sign in to the account that this agent will use for '{slug}'"),
        [verification_url],
    )
}

fn authorization_completed_items(slug: &str) -> [SequenceItem; 1] {
    [SequenceItem::section("Agent account connected", [slug])]
}

fn client_credentials_created_items(
    slug: &str,
    grant_type: OAuthGrantType,
    scopes: &[String],
) -> [SequenceItem; 1] {
    let scopes = if scopes.is_empty() {
        "Scopes: none".to_string()
    } else {
        format!("Scopes: {}", scopes.join(" "))
    };
    [SequenceItem::section(
        "Agent OAuth connection created",
        [
            slug,
            &format!("Grant type: {}", grant_type.as_str()),
            &scopes,
        ],
    )]
}

fn oauth_create_result_items(
    slug: &str,
    redirect_uri: &str,
    scopes: &[String],
    service: Option<&str>,
    verification_url: Option<&str>,
) -> Vec<SequenceItem> {
    if let Some(verification_url) = verification_url {
        return vec![authorization_started_item(slug, verification_url)];
    }
    oauth_created_items(slug, redirect_uri, scopes, service)
}

fn mcp_oauth_create_result_items(
    slug: &str,
    url: &str,
    verification_url: Option<&str>,
) -> Vec<SequenceItem> {
    if let Some(verification_url) = verification_url {
        return vec![authorization_started_item(slug, verification_url)];
    }
    Vec::from(mcp_oauth_created_items(slug, url))
}

const OAUTH_AUTHORIZATION_WAIT_SECONDS: u8 = 25;
const OAUTH_INTERRUPT_POLL_INTERVAL: Duration = Duration::from_millis(50);
const OPEN_BROWSER_PROMPT: &str = "Press Enter to open your browser";

struct BrowserOpenPromptTheme;

impl cliclack::Theme for BrowserOpenPromptTheme {
    fn format_header(&self, state: &cliclack::ThemeState, prompt: &str) -> String {
        if matches!(state, cliclack::ThemeState::Submit) {
            return String::new();
        }

        struct DefaultTheme;
        impl cliclack::Theme for DefaultTheme {}
        cliclack::Theme::format_header(&DefaultTheme, state, prompt)
    }

    fn format_select_item(
        &self,
        _state: &cliclack::ThemeState,
        _selected: bool,
        _label: &str,
        _hint: &str,
    ) -> String {
        String::new()
    }

    fn format_footer_with_message(&self, _state: &cliclack::ThemeState, _message: &str) -> String {
        String::new()
    }
}

struct BrowserOpenPromptThemeReset;

impl Drop for BrowserOpenPromptThemeReset {
    fn drop(&mut self) {
        cliclack::reset_theme();
    }
}

fn wait_to_open_browser() -> Result<()> {
    cliclack::set_theme(BrowserOpenPromptTheme);
    let _reset_theme = BrowserOpenPromptThemeReset;
    let _: () = cliclack::select(OPEN_BROWSER_PROMPT)
        .item((), "", "")
        .interact()
        .context("failed to wait for browser confirmation")?;
    Ok(())
}

fn confirm_and_open_browser(
    interactive: bool,
    url: &str,
    confirm: impl FnOnce() -> Result<()>,
    open: impl FnOnce(&str),
) -> Result<()> {
    if interactive {
        confirm()?;
        open(url);
    }
    Ok(())
}

fn oauth_cancellation() -> anyhow::Error {
    io::Error::from(io::ErrorKind::Interrupted).into()
}

fn receive_oauth_status<T>(receiver: &Receiver<Result<T>>, interrupt: &Interrupt) -> Result<T> {
    loop {
        if interrupt.is_requested() {
            return Err(oauth_cancellation());
        }
        match receiver.recv_timeout(OAUTH_INTERRUPT_POLL_INTERVAL) {
            Ok(result) => return result,
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => {
                bail!("OAuth authorization status check stopped unexpectedly.")
            }
        }
    }
}

/// The session's next status, long-polling Agent Auth on a thread so Ctrl-C
/// stays responsive.
pub(crate) fn wait_for_oauth_status(
    client: &langsmith::Client,
    session_id: &str,
    interrupt: &Interrupt,
) -> Result<String> {
    wait_for_oauth_session(client, session_id, interrupt).map(|session| session.status)
}

/// The session once its status changes, e.g. with the `error_code` of a
/// failed one, long-polling like [`wait_for_oauth_status`].
pub(crate) fn wait_for_oauth_session(
    client: &langsmith::Client,
    session_id: &str,
    interrupt: &Interrupt,
) -> Result<langsmith::OAuthAuthorizationSession> {
    if interrupt.is_requested() {
        return Err(oauth_cancellation());
    }
    let client = client.clone();
    let session_id = session_id.to_string();
    let (sender, receiver) = mpsc::channel();
    // Keep the 25-second server long poll without making Ctrl-C wait for it.
    std::thread::spawn(move || {
        let result = client
            .get_oauth_authorization_session(&session_id, OAUTH_AUTHORIZATION_WAIT_SECONDS)
            .context("failed to check the OAuth authorization session");
        let _ = sender.send(result);
    });
    receive_oauth_status(&receiver, interrupt)
}

/// What a wait on an OAuth authorization session says when it ends badly.
pub(crate) struct OAuthWaitMessages {
    pub(crate) failed: &'static str,
    pub(crate) expired: &'static str,
    /// Followed by the status.
    pub(crate) unexpected: &'static str,
}

const SIGN_IN_MESSAGES: OAuthWaitMessages = OAuthWaitMessages {
    failed: "Agent account sign-in failed; try again.",
    expired: "Agent account sign-in expired; run the command again.",
    unexpected: "Agent account sign-in returned an unexpected status",
};

fn poll_oauth_authorization(
    initial_status: &str,
    next_status: impl FnMut() -> Result<String>,
) -> Result<()> {
    poll_oauth_session(initial_status, next_status, &SIGN_IN_MESSAGES)
}

/// Follow a session from `initial_status` until it completes.
pub(crate) fn poll_oauth_session(
    initial_status: &str,
    mut next_status: impl FnMut() -> Result<String>,
    messages: &OAuthWaitMessages,
) -> Result<()> {
    let mut status = initial_status.to_string();
    loop {
        match status.as_str() {
            "pending" => status = next_status()?,
            "completed" => return Ok(()),
            "failed" => bail!(messages.failed),
            "expired" => bail!(messages.expired),
            other => bail!("{}: {other}.", messages.unexpected),
        }
    }
}

#[expect(clippy::print_stderr, reason = "non-interactive sign-in status stream")]
fn complete_agent_oauth_authorization(
    client: &langsmith::Client,
    session: &langsmith::OAuthAuthorizationSession,
    interrupt: &Interrupt,
) -> Result<()> {
    if interrupt.is_requested() {
        return Err(oauth_cancellation());
    }
    if session.status == "completed" {
        return Ok(());
    }
    let interactive = io::stdin().is_terminal() && io::stderr().is_terminal();
    confirm_and_open_browser(
        interactive,
        &session.verification_url,
        wait_to_open_browser,
        langsmith::login::open_browser,
    )?;

    let progress = interactive.then(cliclack::spinner);
    if let Some(progress) = &progress {
        progress.start("Waiting for you to finish signing in...");
    } else {
        eprintln!("Waiting for you to finish signing in...");
    }

    let result = poll_oauth_authorization(&session.status, || {
        wait_for_oauth_status(client, &session.id, interrupt)
    });
    match result {
        Ok(()) => {
            if let Some(progress) = progress {
                progress.clear();
            }
            Ok(())
        }
        Err(error) => {
            if let Some(progress) = progress {
                if is_cancellation(&error) {
                    progress.cancel("Sign-in cancelled");
                } else {
                    progress.error("Sign-in did not complete");
                }
            } else {
                eprintln!("Sign-in did not complete");
            }
            Err(error)
        }
    }
}

fn show_oauth_create_result(
    client: &langsmith::Client,
    connection_id: &str,
    slug: &str,
    result_items: &[SequenceItem],
    authorization: Option<&langsmith::OAuthAuthorizationSession>,
) -> Result<()> {
    let Some(authorization) = authorization else {
        return show_sequence(result_items);
    };
    let authorization_result = Interrupt::install().and_then(|interrupt| {
        show_sequence(result_items)
            .and_then(|()| complete_agent_oauth_authorization(client, authorization, &interrupt))
    });
    rollback_on_authorization_failure(
        authorization_result,
        || client.delete_connection(connection_id),
        slug,
    )?;
    show_sequence(&authorization_completed_items(slug))
}

fn should_show_oauth_registration_guidance(client_id: Option<&str>) -> bool {
    client_id.is_none()
}

fn rollback_on_authorization_failure<T>(
    authorization: Result<T>,
    delete_connection: impl FnOnce() -> Result<bool>,
    slug: &str,
) -> Result<T> {
    match authorization {
        Ok(value) => Ok(value),
        Err(authorization_error) => match delete_connection() {
            Ok(_) => Err(authorization_error),
            Err(delete_error) => {
                let message = format!(
                    "OAuth authorization failed and mda could not remove the new connection \
                     '{slug}'. Delete it with `mda connections delete {slug}`. Cleanup error: \
                     {delete_error:#}"
                );
                if is_cancellation(&authorization_error) {
                    Err(anyhow::anyhow!(message))
                } else {
                    Err(authorization_error.context(message))
                }
            }
        },
    }
}

fn start_agent_oauth_authorization<T>(
    deployment_id: Option<&str>,
    create_authorization: impl FnOnce(&str) -> Result<T>,
    delete_connection: impl FnOnce() -> Result<bool>,
    slug: &str,
) -> Result<Option<T>> {
    let Some(deployment_id) = deployment_id else {
        return Ok(None);
    };
    rollback_on_authorization_failure(create_authorization(deployment_id), delete_connection, slug)
        .map(Some)
}

fn require_completed_client_credentials_session(
    session: &langsmith::OAuthAuthorizationSession,
) -> Result<()> {
    if session.status == "completed" {
        Ok(())
    } else {
        bail!(
            "OAuth client credentials token acquisition returned status '{}'.",
            session.status
        )
    }
}
fn no_connections_found_items() -> [SequenceItem; 1] {
    [SequenceItem::section(
        "No connections found",
        ["This workspace has no connections."],
    )]
}

fn connection_deleted_items(slug: &str, deleted: bool) -> [SequenceItem; 1] {
    let heading = if deleted {
        "Connection deleted"
    } else {
        "Connection already absent"
    };
    [SequenceItem::section(heading, [slug])]
}

fn connection_kept_items(slug: &str) -> [SequenceItem; 1] {
    [SequenceItem::section("Connection kept", [slug])]
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CreateIntent {
    OAuth,
    Secret,
    InferMcp,
}

fn create_intent(
    oauth: Option<&OAuthCreateOptions>,
    value: &ValueSource,
    piped_stdin_secret: bool,
) -> CreateIntent {
    if oauth.is_some_and(oauth_create_requested) {
        CreateIntent::OAuth
    } else if value_source_is_explicit(value) || piped_stdin_secret {
        CreateIntent::Secret
    } else {
        CreateIntent::InferMcp
    }
}

fn resolve_create_intent(opts: &CreateOptions) -> Result<(CreateIntent, Option<String>)> {
    match create_intent(opts.oauth.as_ref(), &opts.value, false) {
        CreateIntent::OAuth => Ok((CreateIntent::OAuth, None)),
        CreateIntent::Secret => Ok((CreateIntent::Secret, None)),
        CreateIntent::InferMcp => {
            let piped = read_piped_stdin_secret()?;
            Ok((
                create_intent(opts.oauth.as_ref(), &opts.value, piped.is_some()),
                piped,
            ))
        }
    }
}

pub fn create(opts: CreateOptions) -> Result<CommandCompletion> {
    let mcp_url = opts
        .mcp
        .as_deref()
        .map(normalize_mcp_server_url)
        .transpose()?;
    let (intent, stdin_secret) = resolve_create_intent(&opts)?;
    if mcp_url.is_some() && matches!(intent, CreateIntent::OAuth | CreateIntent::Secret) {
        return Err(categorized(
            ErrorCategory::UserInput,
            "--mcp cannot be combined with --oauth, --authorize-url, --token-url, \
             --client-id, or a secret value.",
        ));
    }
    let inferred_mcp = if mcp_url.is_some() || intent != CreateIntent::InferMcp {
        None
    } else {
        inferred_user_owned_mcp_connection_for_target(&opts.target, &opts.slug)?
    };
    let is_oauth = intent == CreateIntent::OAuth || mcp_url.is_some() || inferred_mcp.is_some();
    if opts.authorize && !is_oauth {
        return Err(categorized(
            ErrorCategory::UserInput,
            "--authorize requires OAuth connection flags such as --oauth <service>.",
        ));
    }
    let owner = resolve_create_owner_policy(&opts, is_oauth)?;
    if let Some(url) = mcp_url
        .as_deref()
        .or_else(|| inferred_mcp.as_ref().map(|mcp| mcp.url.as_str()))
    {
        return create_mcp_oauth(&opts, owner, url);
    }
    if is_oauth {
        return create_oauth(opts, owner);
    }
    if owner == ConnectionOwnerPolicy::User {
        if intent == CreateIntent::Secret {
            return Err(categorized(
                ErrorCategory::UserInput,
                "user-owned opaque connections are slots only. Omit a secret value; \
                 per-caller values are collected at runtime.",
            ));
        }
        let env_vars = env_file::parse_env_file(&opts.target.path.join(".env"));
        let session = Session::open(&opts.target, &env_vars)?;
        session
            .client
            .create_workspace_connection(&opts.slug, owner, None, None)
            .map_err(|err| explain(err, &opts.slug))?;
        show_sequence(&user_slot_created_items(&opts.slug))?;
        return Ok(CommandCompletion::Succeeded);
    }
    let env_vars = env_file::parse_env_file(&opts.target.path.join(".env"));
    let resolved = if let Some(value) = stdin_secret {
        ResolvedValue {
            value,
            source: "stdin".to_string(),
        }
    } else {
        resolve_value(
            &opts.value,
            &env_vars,
            "Secret value",
            "mda connections create <slug> < key.txt",
        )?
    };
    let session = Session::open(&opts.target, &env_vars)?;
    let agent_owner_id = require_managed_deployment_id(&session, &opts.target)?;
    session
        .client
        .create_workspace_connection(
            &opts.slug,
            owner,
            Some(&agent_owner_id),
            Some(resolved.value.as_str()),
        )
        .map_err(|err| explain(err, &opts.slug))?;
    show_sequence(&agent_created_items(&opts.slug, &resolved.source))?;
    Ok(CommandCompletion::Succeeded)
}

/// Resolve the project deployment that must own a shared opaque secret.
///
/// Agent-owned secrets are readable with the deployment's `deployments:read`
/// key, so create refuses to store one until that MDA deployment exists.
fn require_managed_deployment_id(session: &Session, target: &ConnectionTarget) -> Result<String> {
    let project = project::load(&target.path)?;
    let name = agent_config::agent_assistant_id(&project)?;
    let deployments = session
        .client
        .list_deployments(&name)
        .context("failed to check for an existing deployment")?;
    managed_deployment_id(&deployments, &name, |id| {
        session
            .client
            .get_deployment(id)
            .context("failed to read the existing deployment")
    })
}

/// Id of the project's deployment, refused when it is known not to be managed.
///
/// List rows are trimmed, so a missing marker means unknown rather than "not
/// managed": read the full deployment before deciding, and accept one the host
/// backend marks neither way. Only an explicit `false` is a refusal.
fn managed_deployment_id(
    deployments: &Value,
    name: &str,
    full_deployment: impl FnOnce(&str) -> Result<Value>,
) -> Result<String> {
    let Some(listed) = find_deployment_by_name(deployments, name) else {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!(
                "No deployment named '{name}' exists in this workspace. \
                 Deploy it with `mda deploy` first."
            ),
        ));
    };
    let id = listed
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            categorized(
                ErrorCategory::Remote,
                format!("deployment '{name}' is missing an id"),
            )
        })?
        .to_string();
    let managed = match deployment_managed_deep_agent_marker(listed) {
        Some(marker) => marker,
        // Absent on the full object too means the backend reports the marker
        // nowhere, which is no reason to refuse a deployment this project named.
        None => deployment_managed_deep_agent_marker(&full_deployment(&id)?).unwrap_or(true),
    };
    if !managed {
        return Err(not_managed_deep_agent(name));
    }
    Ok(id)
}

fn not_managed_deep_agent(name: &str) -> anyhow::Error {
    categorized(
        ErrorCategory::UserInput,
        format!(
            "A deployment named '{name}' exists but is not a Managed Deep Agent. \
             Recreate it with `mda deploy`."
        ),
    )
}

fn create_mcp_oauth(
    opts: &CreateOptions,
    _owner: ConnectionOwnerPolicy,
    url: &str,
) -> Result<CommandCompletion> {
    let env_vars = env_file::parse_env_file(&opts.target.path.join(".env"));
    reject_mcp_address_langsmith_cannot_reach(url, &resolved_host_url(&opts.target, &env_vars))?;
    let session = Session::open(&opts.target, &env_vars)?;
    let deployment_id = opts
        .authorize
        .then(|| require_managed_deployment_id(&session, &opts.target))
        .transpose()?;
    let created = create_discovered_mcp_connection(&session.client, &opts.slug, url)?;
    let authorization = start_agent_oauth_authorization(
        deployment_id.as_deref(),
        |deployment_id| {
            session
                .client
                .create_agent_oauth_authorization_session(&created.connection_id, deployment_id)
        },
        || session.client.delete_connection(&created.connection_id),
        &opts.slug,
    )?;
    let result_items = mcp_oauth_create_result_items(
        &opts.slug,
        url,
        authorization
            .as_ref()
            .map(|session| session.verification_url.as_str()),
    );
    show_oauth_create_result(
        &session.client,
        &created.connection_id,
        &opts.slug,
        &result_items,
        authorization.as_ref(),
    )?;
    Ok(CommandCompletion::Succeeded)
}

fn create_oauth(opts: CreateOptions, owner: ConnectionOwnerPolicy) -> Result<CommandCompletion> {
    let oauth = opts
        .oauth
        .as_ref()
        .filter(|oauth| oauth_create_requested(oauth))
        .ok_or_else(|| categorized(ErrorCategory::UserInput, "OAuth create flags are required."))?;
    let grant_type = resolve_oauth_grant_type(oauth)?;
    if grant_type == OAuthGrantType::ClientCredentials {
        validate_client_credentials_flags(oauth)?;
    }
    if value_source_is_explicit(&opts.value) && oauth.client_id.is_none() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth create cannot read --secret-from-env or --secret-from-file unless also \
             supplying --client-id for BYOT credentials.",
        ));
    }
    let endpoints = resolve_oauth_endpoints(oauth)?;
    let manual = oauth_manual_endpoints_requested(oauth);
    if manual && grant_type == OAuthGrantType::AuthorizationCode {
        if oauth.authorize_url.is_none() || oauth.token_url.is_none() {
            return Err(categorized(
                ErrorCategory::UserInput,
                "manual OAuth create requires both --authorize-url and --token-url.",
            ));
        }
    } else if oauth.service.is_none() && oauth.token_url.is_none() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth create requires --oauth <service>, or manual OAuth endpoint flags.",
        ));
    }
    if grant_type == OAuthGrantType::ClientCredentials
        && trimmed(endpoints.token_url.as_deref()).is_none()
    {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth client credentials requires --token-url or a catalog service with a token URL.",
        ));
    }
    if grant_type == OAuthGrantType::ClientCredentials
        && trimmed(oauth.client_id.as_deref()).is_none()
    {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth client credentials requires --client-id.",
        ));
    }
    if grant_type == OAuthGrantType::ClientCredentials
        && !matches!(
            endpoints.token_endpoint_auth_method.as_str(),
            "client_secret_basic" | "client_secret_post"
        )
    {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth client credentials requires --auth-method client_secret_basic or client_secret_post.",
        ));
    }
    if endpoints.is_public_client() && value_source_is_explicit(&opts.value) {
        return Err(categorized(
            ErrorCategory::UserInput,
            "--auth-method none registers a public client, which stores no secret, so it \
             cannot be combined with --secret-from-env or --secret-from-file.",
        ));
    }
    let token_params = parse_token_params(&oauth.token_params)?;
    let token_request_headers = parse_token_request_headers(&oauth.token_request_headers)?;
    let env_vars = env_file::parse_env_file(&opts.target.path.join(".env"));
    let session = Session::open(&opts.target, &env_vars)?;
    let deployment_id = (opts.authorize || grant_type == OAuthGrantType::ClientCredentials)
        .then(|| require_managed_deployment_id(&session, &opts.target))
        .transpose()?;
    let redirect_uri = (grant_type == OAuthGrantType::AuthorizationCode)
        .then(|| session.client.oauth_redirect_uri())
        .transpose()?;
    if grant_type == OAuthGrantType::AuthorizationCode
        && should_show_oauth_registration_guidance(oauth.client_id.as_deref())
    {
        continue_sequence(&oauth_redirect_uri_items(
            redirect_uri.as_deref().unwrap_or_default(),
            endpoints.client_registration_url.as_deref(),
        ))?;
    }
    let client_secret = if endpoints.is_public_client() {
        None
    } else if oauth_client_secret_requested(oauth, &opts.value) {
        Some(
            resolve_value(
                &opts.value,
                &env_vars,
                "OAuth client secret",
                "mda connections create <slug> < key.txt",
            )?
            .value,
        )
    } else {
        None
    };
    if grant_type == OAuthGrantType::ClientCredentials && client_secret.is_none() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth client credentials requires a client secret from --secret-from-env, --secret-from-file, or stdin.",
        ));
    }
    let created = session
        .client
        .create_oauth_workspace_connection(CreateOAuthConnection {
            slug: &opts.slug,
            owner_policy: owner,
            service: endpoints.service,
            client_id: oauth.client_id.as_deref(),
            client_secret: client_secret.as_deref(),
            display_name: endpoints.display_name.unwrap_or(&opts.slug),
            scopes: &endpoints.scopes,
            allowed_scopes: &endpoints.allowed_scopes,
            authorize_url: endpoints.authorize_url.as_deref(),
            token_url: endpoints.token_url.as_deref(),
            token_endpoint_auth_method: &endpoints.token_endpoint_auth_method,
            authorization_params: &endpoints.authorization_params,
            grant_type: grant_type.as_str(),
            token_params: &token_params,
            token_request_headers: &token_request_headers,
            client_registration_url: endpoints.client_registration_url.as_deref(),
            mcp_server_url: None,
        })
        .map_err(|err| explain(err, &opts.slug))?;
    if grant_type == OAuthGrantType::ClientCredentials {
        let authorization = start_agent_oauth_authorization(
            deployment_id.as_deref(),
            |deployment_id| {
                session
                    .client
                    .create_agent_oauth_authorization_session(&created.connection_id, deployment_id)
                    .map_err(|error| match client_secret.as_deref() {
                        Some(secret) => langsmith::redact_secret(error, secret),
                        None => error,
                    })
                    .map_err(|error| {
                        token_request_headers
                            .values()
                            .filter_map(Value::as_str)
                            .fold(error, langsmith::redact_secret)
                    })
            },
            || session.client.delete_connection(&created.connection_id),
            &opts.slug,
        )?
        .context("OAuth client credentials requires a managed deployment")?;
        rollback_on_authorization_failure(
            require_completed_client_credentials_session(&authorization),
            || session.client.delete_connection(&created.connection_id),
            &opts.slug,
        )?;
        show_sequence(&client_credentials_created_items(
            &opts.slug,
            grant_type,
            &endpoints.scopes,
        ))?;
        return Ok(CommandCompletion::Succeeded);
    }
    let authorization = start_agent_oauth_authorization(
        deployment_id.as_deref(),
        |deployment_id| {
            session
                .client
                .create_agent_oauth_authorization_session(&created.connection_id, deployment_id)
        },
        || session.client.delete_connection(&created.connection_id),
        &opts.slug,
    )?;
    let result_items = oauth_create_result_items(
        &opts.slug,
        redirect_uri.as_deref().unwrap_or_default(),
        &endpoints.scopes,
        endpoints.service,
        authorization
            .as_ref()
            .map(|session| session.verification_url.as_str())
            .filter(|url| !url.is_empty()),
    );
    show_oauth_create_result(
        &session.client,
        &created.connection_id,
        &opts.slug,
        &result_items,
        authorization.as_ref(),
    )?;
    Ok(CommandCompletion::Succeeded)
}

fn validate_client_credentials_flags(oauth: &OAuthCreateOptions) -> Result<()> {
    if oauth.authorize_url.is_some()
        || !oauth.authorization_params.is_empty()
        || !oauth.allowed_scopes.is_empty()
    {
        return Err(categorized(
            ErrorCategory::UserInput,
            "OAuth client credentials cannot use --authorize-url, --authorization-param, or --allowed-scope.",
        ));
    }
    Ok(())
}

/// Create a user-owned OAuth connection by asking Agent Auth to discover the
/// MCP server's OAuth metadata and dynamically register a client.
pub(crate) fn create_discovered_mcp_connection(
    client: &langsmith::Client,
    slug: &str,
    mcp_server_url: &str,
) -> Result<ConnectionSummary> {
    client
        .create_oauth_workspace_connection(CreateOAuthConnection {
            slug,
            owner_policy: ConnectionOwnerPolicy::User,
            service: None,
            client_id: None,
            client_secret: None,
            display_name: slug,
            scopes: &[],
            allowed_scopes: &[],
            authorize_url: None,
            token_url: None,
            token_endpoint_auth_method: "none",
            authorization_params: &Map::new(),
            grant_type: OAuthGrantType::AuthorizationCode.as_str(),
            token_params: &Map::new(),
            token_request_headers: &Map::new(),
            client_registration_url: None,
            mcp_server_url: Some(mcp_server_url),
        })
        .map_err(|err| explain_mcp_dcr(err, slug, mcp_server_url))
}

fn inferred_user_owned_mcp_connection_for_target(
    target: &ConnectionTarget,
    slug: &str,
) -> Result<Option<compile::McpConnectionEntry>> {
    let project = match project::load(&target.path) {
        Ok(project) => project,
        Err(err) if category_of(&err) == ErrorCategory::Project => return Ok(None),
        Err(err) => return Err(err),
    };
    let out_dir = target.path.join(".mda").join("build");
    let installation = Installation::from_env()?;
    let matches = compile::mcp_connection_entries_for_project(
        &project,
        &out_dir,
        installation.runtime_source(),
    )?;
    select_user_owned_mcp_connection(matches, slug)
}

fn select_user_owned_mcp_connection(
    connections: Vec<compile::McpConnectionEntry>,
    slug: &str,
) -> Result<Option<compile::McpConnectionEntry>> {
    let matches: Vec<compile::McpConnectionEntry> = connections
        .into_iter()
        .filter(|entry| {
            entry.slug == slug && entry.owner_policy == ConnectionOwnerPolicy::User.as_str()
        })
        .collect();
    match matches.as_slice() {
        [] => Ok(None),
        [entry] => Ok(Some(entry.clone())),
        many => Err(categorized(
            ErrorCategory::UserInput,
            format!(
                "connection '{slug}' is used by multiple user-owned MCP servers in this project: {}. \
                 Keep the slug unique per MCP server, or create the connection with explicit OAuth flags.",
                many.iter()
                    .map(|entry| format!("{}::{}", entry.connector_name, entry.server_name))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        )),
    }
}

#[cfg_attr(test, derive(Debug))]
struct ResolvedOAuthEndpoints {
    service: Option<&'static str>,
    display_name: Option<&'static str>,
    authorize_url: Option<String>,
    token_url: Option<String>,
    token_endpoint_auth_method: String,
    authorization_params: Map<String, Value>,
    scopes: Vec<String>,
    allowed_scopes: Vec<String>,
    client_registration_url: Option<String>,
}

impl ResolvedOAuthEndpoints {
    fn is_public_client(&self) -> bool {
        self.token_endpoint_auth_method == catalog::PUBLIC_CLIENT
    }
}

fn resolve_oauth_endpoints(oauth: &OAuthCreateOptions) -> Result<ResolvedOAuthEndpoints> {
    let grant_type = resolve_oauth_grant_type(oauth)?;
    let entry = match trimmed(oauth.service.as_deref()) {
        Some(service) => Some(catalog::find(service).ok_or_else(|| {
            categorized(
                ErrorCategory::UserInput,
                format!(
                    "'{service}' is not a known OAuth service. Supported: {}. See \
                     `mda connections catalog`, or pass --authorize-url and --token-url for a \
                     service the catalog does not cover.",
                    catalog::service_names()
                ),
            )
        })?),
        None => None,
    };

    let mut authorization_params: Map<String, Value> = entry
        .filter(|_| grant_type == OAuthGrantType::AuthorizationCode)
        .map(|entry| {
            entry
                .authorization_params
                .iter()
                .map(|(key, value)| ((*key).to_string(), Value::String((*value).to_string())))
                .collect()
        })
        .unwrap_or_default();
    for (key, value) in parse_authorization_params(&oauth.authorization_params)? {
        authorization_params.insert(key, value);
    }

    let scopes = if oauth.scopes.is_empty() && grant_type == OAuthGrantType::AuthorizationCode {
        entry
            .map(|entry| {
                entry
                    .default_scopes
                    .iter()
                    .map(|scope| (*scope).to_string())
                    .collect()
            })
            .unwrap_or_default()
    } else {
        oauth.scopes.clone()
    };
    let allowed_scopes = if oauth.allowed_scopes.is_empty() {
        scopes.clone()
    } else {
        let outside = scopes_outside(&scopes, &oauth.allowed_scopes);
        if !outside.is_empty() {
            return Err(categorized(
                ErrorCategory::UserInput,
                format!(
                    "--scope {} is not in --allowed-scope, which the server requires to be the \
                     ceiling. Add it to --allowed-scope or drop it from --scope.",
                    outside.join(", ")
                ),
            ));
        }
        oauth.allowed_scopes.clone()
    };

    let token_endpoint_auth_method = match trimmed(oauth.auth_method.as_deref()) {
        Some(method) => method.to_string(),
        None => entry
            .map(|entry| entry.token_endpoint_auth_method.to_string())
            .unwrap_or_else(|| catalog::DEFAULT_AUTH_METHOD.to_string()),
    };

    let (mut authorize_url, token_url) = if oauth_manual_endpoints_requested(oauth) {
        (oauth.authorize_url.clone(), oauth.token_url.clone())
    } else {
        (
            entry.map(|entry| entry.authorization_url.to_string()),
            entry.map(|entry| entry.token_url.to_string()),
        )
    };

    if grant_type == OAuthGrantType::ClientCredentials {
        authorize_url = None;
        authorization_params.clear();
    }

    Ok(ResolvedOAuthEndpoints {
        service: entry.map(|entry| entry.service),
        display_name: entry.map(|entry| entry.display_name),
        authorize_url,
        token_url,
        token_endpoint_auth_method,
        authorization_params,
        scopes,
        allowed_scopes,
        client_registration_url: entry.map(|entry| entry.client_registration_url.to_string()),
    })
}

fn resolve_oauth_grant_type(oauth: &OAuthCreateOptions) -> Result<OAuthGrantType> {
    match trimmed(oauth.grant_type.as_deref()) {
        None | Some("authorization_code") => Ok(OAuthGrantType::AuthorizationCode),
        Some("client_credentials") => Ok(OAuthGrantType::ClientCredentials),
        Some(value) => Err(categorized(
            ErrorCategory::UserInput,
            format!(
                "unsupported OAuth grant type '{value}'; expected authorization_code or client_credentials"
            ),
        )),
    }
}

fn trimmed(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn scopes_outside<'a>(requested: &'a [String], allowed: &[String]) -> Vec<&'a str> {
    requested
        .iter()
        .filter(|scope| !allowed.contains(scope))
        .map(String::as_str)
        .collect()
}

fn parse_authorization_params(pairs: &[String]) -> Result<Map<String, Value>> {
    parse_key_value_params(pairs, "--authorization-param", &[])
}

fn parse_token_params(pairs: &[String]) -> Result<Map<String, Value>> {
    const RESERVED: &[&str] = &["grant_type", "client_id", "client_secret", "scope"];
    parse_key_value_params(pairs, "--token-param", RESERVED)
}

fn parse_token_request_headers(pairs: &[String]) -> Result<Map<String, Value>> {
    const RESERVED: &[&str] = &["host", "content-type", "content-length"];
    let mut headers = Map::new();
    // Never put input in these errors: even malformed names can contain secrets.
    for pair in pairs {
        let (name, value) = pair.split_once('=').ok_or_else(|| {
            categorized(
                ErrorCategory::UserInput,
                "--token-request-header requires KEY=VALUE",
            )
        })?;
        let name = name.trim();
        if name.is_empty() {
            return Err(categorized(
                ErrorCategory::UserInput,
                "--token-request-header requires a non-empty header name",
            ));
        }
        if RESERVED
            .iter()
            .any(|reserved| name.eq_ignore_ascii_case(reserved))
        {
            return Err(categorized(
                ErrorCategory::UserInput,
                "--token-request-header contains a reserved header",
            ));
        }
        headers.insert(name.to_string(), Value::String(value.to_string()));
    }
    Ok(headers)
}

fn parse_key_value_params(
    pairs: &[String],
    flag: &str,
    reserved: &[&str],
) -> Result<Map<String, Value>> {
    let mut params = Map::new();
    for pair in pairs {
        let Some((key, value)) = pair.split_once('=') else {
            return Err(categorized(
                ErrorCategory::UserInput,
                format!("{flag} takes KEY=VALUE, but got '{pair}'"),
            ));
        };
        let key = key.trim();
        if key.is_empty() {
            return Err(categorized(
                ErrorCategory::UserInput,
                format!("{flag} needs a key before the '=', but got '{pair}'"),
            ));
        }
        if reserved
            .iter()
            .any(|reserved| key.eq_ignore_ascii_case(reserved))
        {
            return Err(categorized(
                ErrorCategory::UserInput,
                format!("{flag} cannot set reserved OAuth parameter '{key}'"),
            ));
        }
        params.insert(key.to_string(), Value::String(value.to_string()));
    }
    Ok(params)
}

fn oauth_manual_endpoints_requested(oauth: &OAuthCreateOptions) -> bool {
    oauth.authorize_url.is_some() || oauth.token_url.is_some()
}

fn oauth_create_requested(oauth: &OAuthCreateOptions) -> bool {
    oauth.service.is_some()
        || oauth.grant_type.is_some()
        || oauth.client_id.is_some()
        || oauth.authorize_url.is_some()
        || oauth.token_url.is_some()
        || oauth.auth_method.is_some()
        || !oauth.scopes.is_empty()
        || !oauth.allowed_scopes.is_empty()
        || !oauth.authorization_params.is_empty()
        || !oauth.token_params.is_empty()
        || !oauth.token_request_headers.is_empty()
}

fn oauth_client_secret_requested(oauth: &OAuthCreateOptions, value: &ValueSource) -> bool {
    oauth.client_id.is_some() || value_source_is_explicit(value)
}

fn resolve_create_owner_policy(
    opts: &CreateOptions,
    is_oauth: bool,
) -> Result<ConnectionOwnerPolicy> {
    let manifest = manifest_owner_policy_for_slug(&opts.target, &opts.slug)?;
    let grant_type = opts
        .oauth
        .as_ref()
        .map(resolve_oauth_grant_type)
        .transpose()?
        .unwrap_or_default();
    resolve_owner_from_manifest_and_kind(manifest, is_oauth, opts.authorize, grant_type)
}

fn resolve_owner_from_manifest_and_kind(
    manifest: Option<ConnectionOwnerPolicy>,
    is_oauth: bool,
    authorize: bool,
    grant_type: OAuthGrantType,
) -> Result<ConnectionOwnerPolicy> {
    if is_oauth && grant_type == OAuthGrantType::ClientCredentials {
        if manifest == Some(ConnectionOwnerPolicy::User) {
            return Err(categorized(
                ErrorCategory::UserInput,
                "OAuth client credentials connections are agent-owned. Change the connector declaration to owner agent.",
            ));
        }
        return Ok(ConnectionOwnerPolicy::Agent);
    }
    if is_oauth && authorize {
        return Ok(manifest.unwrap_or(ConnectionOwnerPolicy::User));
    }
    let required = if is_oauth {
        ConnectionOwnerPolicy::User
    } else {
        ConnectionOwnerPolicy::Agent
    };
    if manifest.is_some_and(|owner| owner != required) {
        let message = if is_oauth {
            "OAuth connections are user-owned. Change the connector declaration to owner user."
        } else {
            "Fixed secrets are agent-owned. Change the connector declaration to owner agent."
        };
        return Err(categorized(ErrorCategory::UserInput, message));
    }
    Ok(required)
}

fn connector_requirements_for_loaded_project(
    project: &project::Project,
    target: &ConnectionTarget,
) -> Result<compile::ConnectorRequirements> {
    let out_dir = target.path.join(".mda").join("build");
    let installation = Installation::from_env()?;
    compile::connector_requirements_for_project(project, &out_dir, installation.runtime_source())
}

fn manifest_owner_policy_for_slug(
    target: &ConnectionTarget,
    slug: &str,
) -> Result<Option<ConnectionOwnerPolicy>> {
    let project = match project::load(&target.path) {
        Ok(project) => project,
        Err(err) if category_of(&err) == ErrorCategory::Project => return Ok(None),
        Err(err) => return Err(err),
    };
    let requirements = connector_requirements_for_loaded_project(&project, target)?;
    match compile::connection_owner_policy_for_slug(&requirements, slug) {
        Some(owner) => Ok(Some(owner)),
        None if agent_and_user_manifest_overlap(&requirements, slug) => Err(categorized(
            ErrorCategory::UserInput,
            format!(
                "connection '{slug}' is declared as both agent-owned and user-owned in this \
                 project. Use one owner per slug in connectors."
            ),
        )),
        None => Ok(None),
    }
}

fn agent_and_user_manifest_overlap(
    requirements: &compile::ConnectorRequirements,
    slug: &str,
) -> bool {
    compile::agent_connections_from_connector_requirements(requirements)
        .iter()
        .any(|entry| entry == slug)
        && compile::user_connections_from_connector_requirements(requirements)
            .iter()
            .any(|entry| entry == slug)
}

pub fn list(opts: ListOptions) -> Result<CommandCompletion> {
    let session = Session::open_at(&opts.target)?;
    let connections = session
        .client
        .list_workspace_connections()
        .map_err(|err| explain(err, ""))?;
    if connections.is_empty() && !opts.json {
        show_sequence(&no_connections_found_items())?;
        return Ok(CommandCompletion::Succeeded);
    }
    emit(&connections, opts.json)
}

pub fn get(opts: GetOptions) -> Result<CommandCompletion> {
    let session = Session::open_at(&opts.target)?;
    let found = session.find(&opts.slug)?;
    emit(std::slice::from_ref(&found), opts.json)
}

pub fn delete(opts: DeleteOptions) -> Result<CommandCompletion> {
    let session = Session::open_at(&opts.target)?;
    let found = session.find(&opts.slug)?;
    if !confirm_delete(&found, opts.yes)? {
        show_sequence(&connection_kept_items(&opts.slug))?;
        return Ok(CommandCompletion::Cancelled);
    }
    let deleted = session
        .client
        .delete_connection(&found.connection_id)
        .map_err(|err| explain(err, &opts.slug))?;
    show_sequence(&connection_deleted_items(&opts.slug, deleted))?;
    Ok(CommandCompletion::Succeeded)
}

impl Session {
    fn open_at(target: &ConnectionTarget) -> Result<Self> {
        let env_vars = env_file::parse_env_file(&target.path.join(".env"));
        Self::open(target, &env_vars)
    }

    fn open(target: &ConnectionTarget, env_vars: &BTreeMap<String, String>) -> Result<Self> {
        let env_path = target.path.join(".env");
        let host_url = resolved_host_url(target, env_vars);
        let host_url = host_url.as_str();

        let platform_api_url = resolve_platform_api_url(host_url, env_vars);
        let credential = resolve_api_key(env_vars, &env_path, &platform_api_url)?;
        let workspace_id = require_workspace_id(target, env_vars)?;
        let client = langsmith::Client::with_credential_and_platform(
            host_url,
            Some(platform_api_url.as_str()),
            credential,
            Some(workspace_id),
        )?;
        Ok(Self { client })
    }

    fn find(&self, slug: &str) -> Result<ConnectionSummary> {
        match self
            .client
            .find_workspace_connection_by_slug(slug)
            .map_err(|err| explain(err, slug))?
        {
            Some(found) => Ok(found),
            None => Err(categorized(
                ErrorCategory::UserInput,
                format!(
                    "No connection named '{slug}' in this workspace. See what it \
                     has with `mda connections list`."
                ),
            )),
        }
    }
}

fn require_workspace_id(
    target: &ConnectionTarget,
    env_vars: &BTreeMap<String, String>,
) -> Result<String> {
    resolve_preferred_env(
        WORKSPACE_ID_ENV,
        REMOVED_WORKSPACE_ID_ENVS,
        target.workspace_id.as_deref(),
        env_vars,
        |key| std::env::var(key).ok(),
        " or pass --workspace-id instead",
    )?
    .ok_or_else(|| {
        categorized(
            ErrorCategory::UserInput,
            format!(
                "A workspace ID is required for connection commands. Set {WORKSPACE_ID_ENV} \
                 in the environment or project .env, or pass --workspace-id."
            ),
        )
    })
}

fn confirm_delete(found: &ConnectionSummary, yes: bool) -> Result<bool> {
    if yes {
        return Ok(true);
    }
    if !io::stdin().is_terminal() {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!(
                "Refusing to delete '{}' without confirmation. Re-run with --yes.",
                found.slug
            ),
        ));
    }
    Ok(cliclack::confirm(delete_confirm_prompt(found))
        .initial_value(false)
        .interact()?)
}

fn delete_confirm_prompt(found: &ConnectionSummary) -> String {
    if found.is_secret() {
        format!(
            "Delete '{}'? The secret value cannot be recovered.",
            found.slug
        )
    } else if found.kind.eq_ignore_ascii_case("oauth2") {
        format!(
            "Delete OAuth connection '{}'? Linked grants will stop resolving.",
            found.slug
        )
    } else {
        format!("Delete connection '{}'?", found.slug)
    }
}

fn emit(connections: &[ConnectionSummary], json: bool) -> Result<CommandCompletion> {
    let mut stdout = io::stdout().lock();
    write_connections(&mut stdout, connections, json)?;
    Ok(CommandCompletion::Succeeded)
}

fn write_connections(
    out: &mut impl Write,
    connections: &[ConnectionSummary],
    json: bool,
) -> Result<()> {
    let written = if json {
        serde_json::to_writer_pretty(&mut *out, connections)
            .map_err(io::Error::from)
            .and_then(|()| writeln!(out))
            .and_then(|()| out.flush())
    } else {
        write_table(&mut *out, connections)
    };
    match written {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::BrokenPipe => Ok(()),
        Err(err) => Err(err).context("failed to write connections to stdout"),
    }
}

fn column_width<'a>(header: &str, values: impl Iterator<Item = &'a str>) -> usize {
    values.map(str::len).fold(header.len(), usize::max)
}

fn write_table(out: &mut impl Write, connections: &[ConnectionSummary]) -> io::Result<()> {
    let slug_width = column_width("SLUG", connections.iter().map(|item| item.slug.as_str()));
    let kind_width = column_width("KIND", connections.iter().map(|item| item.kind.as_str()));
    let app_width = column_width(
        "APP",
        connections
            .iter()
            .map(|item| item.oauth_app_slug.as_deref().unwrap_or("—")),
    );
    let created_width = column_width(
        "CREATED",
        connections.iter().map(|item| item.created_at.as_str()),
    );
    writeln!(
        out,
        "{:<slug_width$}  {:<kind_width$}  {:<app_width$}  {:<created_width$}  UPDATED",
        "SLUG", "KIND", "APP", "CREATED"
    )?;
    for item in connections {
        writeln!(
            out,
            "{:<slug_width$}  {:<kind_width$}  {:<app_width$}  {:<created_width$}  {}",
            item.slug,
            item.kind,
            item.oauth_app_slug.as_deref().unwrap_or("—"),
            item.created_at,
            item.updated_at
        )?;
    }
    out.flush()
}

fn normalize_mcp_server_url(raw: &str) -> Result<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "--mcp needs an MCP server URL, for example https://mcp.acme.com/mcp.",
        ));
    }
    let candidate = if has_url_scheme(trimmed) {
        trimmed.to_string()
    } else {
        format!("https://{trimmed}")
    };
    let parsed = reqwest::Url::parse(&candidate).map_err(|_| {
        categorized(
            ErrorCategory::UserInput,
            format!("'{trimmed}' is not a valid MCP server URL."),
        )
    })?;
    match parsed.scheme() {
        "http" | "https" => {}
        other => {
            return Err(categorized(
                ErrorCategory::UserInput,
                format!("--mcp only accepts http or https URLs, not {other}."),
            ));
        }
    }
    if parsed.host_str().is_none() {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!("'{trimmed}' is not a valid MCP server URL."),
        ));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "--mcp URLs cannot include a username or password.",
        ));
    }
    Ok(parsed.to_string())
}

/// The LangSmith host this invocation talks to, before any per-API mapping.
fn resolved_host_url(target: &ConnectionTarget, env_vars: &BTreeMap<String, String>) -> String {
    let (default_host, _) = resolve_langsmith_api_bases(env_vars);
    target
        .host_url
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or(default_host)
}

/// Refuse an MCP address that LangSmith has no route to.
///
/// Discovery and client registration run inside LangSmith, not on this machine,
/// so an address that only resolves on a private network is a dead end unless
/// the backend is local too. Without this the probe answers from the developer's
/// machine and the failure reads as missing CIMD or DCR, which is wrong.
///
/// This is a reachability check, not a security control: a public name that
/// resolves to a private address still reaches Agent Auth, so the SSRF boundary
/// belongs server-side.
fn reject_mcp_address_langsmith_cannot_reach(
    mcp_url: &str,
    langsmith_host_url: &str,
) -> Result<()> {
    if !is_private_address(mcp_url) || is_private_address(langsmith_host_url) {
        return Ok(());
    }
    Err(categorized(
        ErrorCategory::UserInput,
        format!(
            "Could not reach '{mcp_url}'. Make sure the MCP server is available on the public \
             internet."
        ),
    ))
}

/// Whether a URL's host is only reachable from a private network.
///
/// A host that is not an IP literal counts as public. The CLI cannot resolve it
/// the way LangSmith would, and guessing would refuse legitimate servers.
fn is_private_address(url: &str) -> bool {
    let Some(host) = reqwest::Url::parse(url)
        .ok()
        .and_then(|parsed| parsed.host_str().map(str::to_ascii_lowercase))
    else {
        return false;
    };
    if host == "localhost" || host.ends_with(".localhost") {
        return true;
    }
    host.trim_start_matches('[')
        .trim_end_matches(']')
        .parse::<IpAddr>()
        .is_ok_and(is_private_ip)
}

fn is_private_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            v4.is_loopback() || v4.is_private() || v4.is_link_local() || v4.is_unspecified()
        }
        IpAddr::V6(v6) => {
            if let Some(mapped) = v6.to_ipv4_mapped() {
                return is_private_ip(IpAddr::V4(mapped));
            }
            let leading = v6.segments()[0];
            v6.is_loopback()
                || v6.is_unspecified()
                // Unique local (fc00::/7) and link-local unicast (fe80::/10),
                // neither of which std classifies on stable.
                || leading & 0xfe00 == 0xfc00
                || leading & 0xffc0 == 0xfe80
        }
    }
}

fn has_url_scheme(value: &str) -> bool {
    value.split_once("://").is_some_and(|(scheme, rest)| {
        !scheme.is_empty()
            && !rest.is_empty()
            && scheme.bytes().all(|byte| {
                byte.is_ascii_alphabetic() || byte == b'+' || byte == b'.' || byte == b'-'
            })
    })
}

/// How a direct request to the MCP URL answered.
///
/// Agent Auth reports a failed discovery the same way whether the host does not
/// exist or simply registers no client, so a typo reads as missing CIMD or DCR.
/// The CLI asks the server itself to tell the two apart.
#[cfg_attr(test, derive(Debug, PartialEq, Eq))]
enum McpProbe {
    /// No HTTP response at all: unknown host, refused connection, or timeout.
    Unreachable,
    /// The host answered, but nothing is served at that path.
    NotFound,
    /// The host answered. An MCP server rejects an unauthenticated probe, so
    /// any other status still means discovery had something to talk to.
    Responded,
}

const MCP_PROBE_TIMEOUT: Duration = Duration::from_secs(8);

fn probe_mcp_server(url: &str) -> McpProbe {
    let Ok(http) = crate::http::client_builder()
        .timeout(MCP_PROBE_TIMEOUT)
        .build()
    else {
        return McpProbe::Unreachable;
    };
    classify_mcp_probe(http.get(url).send().ok().map(|response| response.status()))
}

fn classify_mcp_probe(status: Option<StatusCode>) -> McpProbe {
    match status {
        None => McpProbe::Unreachable,
        Some(StatusCode::NOT_FOUND) => McpProbe::NotFound,
        Some(_) => McpProbe::Responded,
    }
}

fn explain_mcp_dcr(err: anyhow::Error, slug: &str, mcp_url: &str) -> anyhow::Error {
    let err = explain(err, slug);
    let Some(api) = err
        .chain()
        .find_map(|cause| cause.downcast_ref::<ApiError>())
    else {
        return err;
    };
    if !mcp_dcr_unsupported(api) {
        return err;
    }
    mcp_discovery_error(probe_mcp_server(mcp_url), slug, mcp_url)
}

fn mcp_discovery_error(probe: McpProbe, slug: &str, mcp_url: &str) -> anyhow::Error {
    match probe {
        McpProbe::Unreachable => categorized(
            ErrorCategory::Network,
            format!(
                "'{mcp_url}' did not answer when mda checked it from this machine, so it is \
                 not possible to tell whether it supports CIMD or OAuth Dynamic Client \
                 Registration. Check the host for a typo."
            ),
        ),
        McpProbe::NotFound => categorized(
            ErrorCategory::UserInput,
            format!(
                "'{mcp_url}' answered 404, so that host serves no MCP endpoint at that path. \
                 Check the path for a typo."
            ),
        ),
        McpProbe::Responded => categorized(
            ErrorCategory::Remote,
            format!(
                "'{mcp_url}' does not support CIMD or OAuth Dynamic Client Registration, so \
                 mda cannot register a client automatically. Create the connection with \
                 `mda connections create {slug} --oauth <service>` when the provider is in \
                 `mda connections catalog`, or pass --authorize-url, --token-url, and \
                 --client-id."
            ),
        ),
    }
}

fn mcp_dcr_unsupported(api: &ApiError) -> bool {
    matches!(
        api.status,
        StatusCode::BAD_REQUEST | StatusCode::UNPROCESSABLE_ENTITY | StatusCode::NOT_FOUND
    ) || dcr_unsupported_detail(&api.detail)
}

fn dcr_unsupported_detail(detail: &str) -> bool {
    let detail = detail.to_ascii_lowercase();
    detail.contains("registration_endpoint")
        || detail.contains("dynamic client registration")
        || detail.contains("client id metadata")
        || detail.contains("cimd")
        || detail.contains("oauth discovery")
        || (detail.contains("dcr") && detail.contains("not support"))
}

fn explain(err: anyhow::Error, slug: &str) -> anyhow::Error {
    let Some(api) = err
        .chain()
        .find_map(|cause| cause.downcast_ref::<ApiError>())
    else {
        return err;
    };
    if api.status == StatusCode::CONFLICT && !slug.is_empty() {
        return categorized(
            ErrorCategory::Remote,
            format!(
                "A connection named '{slug}' already exists in this workspace. \
                 Delete it with `mda connections delete {slug}` and create again, or pick a \
                 different slug."
            ),
        );
    }
    if api.status == StatusCode::FORBIDDEN
        && api
            .detail
            .to_ascii_lowercase()
            .contains("org_scoped_key_requires_workspace")
    {
        return categorized(
            ErrorCategory::Permission,
            format!(
                "This API key is not scoped to a workspace, so LangSmith cannot tell \
                 which one to use. Pass --workspace-id, or set {WORKSPACE_ID_ENV}."
            ),
        );
    }
    err
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};

    use super::*;

    #[test]
    fn managed_deployment_id_returns_exact_mda_deployment() {
        let deployments = serde_json::json!({
            "resources": [
                {
                    "id": "dep-other",
                    "name": "mail-agent-copy",
                    "is_managed_deep_agent": true,
                },
                {
                    "id": "dep-mda",
                    "name": "mail-agent",
                    "is_managed_deep_agent": true,
                },
            ],
        });

        assert_eq!(
            managed_deployment_id(&deployments, "mail-agent", |_| {
                panic!("a marked list row needs no re-read")
            })
            .unwrap(),
            "dep-mda"
        );
    }

    #[test]
    fn managed_deployment_id_rereads_a_trimmed_list_row() {
        let deployments = serde_json::json!({
            "resources": [{ "id": "dep-mda", "name": "mail-agent" }],
        });

        assert_eq!(
            managed_deployment_id(&deployments, "mail-agent", |id| {
                assert_eq!(id, "dep-mda");
                Ok(serde_json::json!({ "id": id, "is_managed_deep_agent": true }))
            })
            .unwrap(),
            "dep-mda"
        );
    }

    #[test]
    fn managed_deployment_id_accepts_a_deployment_the_backend_never_marks() {
        let deployments = serde_json::json!({
            "resources": [{ "id": "dep-mda", "name": "mail-agent" }],
        });

        assert_eq!(
            managed_deployment_id(&deployments, "mail-agent", |id| {
                Ok(serde_json::json!({ "id": id }))
            })
            .unwrap(),
            "dep-mda"
        );
    }

    #[test]
    fn managed_deployment_id_rejects_a_full_deployment_marked_not_managed() {
        let deployments = serde_json::json!({
            "resources": [{ "id": "dep-graph", "name": "mail-agent" }],
        });

        let error = managed_deployment_id(&deployments, "mail-agent", |id| {
            Ok(serde_json::json!({ "id": id, "is_managed_deep_agent": false }))
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("is not a Managed Deep Agent"), "{error}");
    }

    #[test]
    fn managed_deployment_id_rejects_a_missing_deployment() {
        let deployments = serde_json::json!({ "resources": [] });
        let error = managed_deployment_id(&deployments, "mail-agent", |_| {
            panic!("a missing deployment needs no re-read")
        })
        .unwrap_err()
        .to_string();

        assert!(
            error.contains("No deployment named 'mail-agent'"),
            "{error}"
        );
        assert!(error.contains("mda deploy"), "{error}");
    }

    #[test]
    fn managed_deployment_id_rejects_non_mda_deployment() {
        let deployments = serde_json::json!({
            "resources": [{
                "id": "dep-graph",
                "name": "mail-agent",
                "is_managed_deep_agent": false,
            }],
        });
        let error = managed_deployment_id(&deployments, "mail-agent", |_| {
            panic!("a marked list row needs no re-read")
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("is not a Managed Deep Agent"), "{error}");
    }

    fn summary(slug: &str, kind: &str, owner: &str) -> ConnectionSummary {
        ConnectionSummary {
            connection_id: "77777777-7777-7777-7777-777777777777".into(),
            slug: slug.into(),
            display_name: slug.into(),
            kind: kind.into(),
            oauth_provider_id: None,
            created_at: "2026-08-27T00:00:00Z".into(),
            updated_at: "2026-08-27T00:00:00Z".into(),
            oauth_app_slug: None,
            service: None,
            owner_policy: Some(owner.into()),
        }
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

    #[test]
    fn oauth_provider_secret_does_not_make_connection_agent_owned() {
        assert_eq!(
            resolve_owner_from_manifest_and_kind(
                None,
                true,
                false,
                OAuthGrantType::AuthorizationCode,
            )
            .unwrap(),
            ConnectionOwnerPolicy::User
        );
    }

    #[test]
    fn authorized_oauth_create_reports_one_complete_sequence() {
        assert_eq!(
            oauth_create_result_items(
                "github",
                "https://api.smith.langchain.com/v1/agent-auth/oauth/callback",
                &["repo".to_string()],
                Some("github"),
                Some("https://github.com/login/oauth/authorize?state=session"),
            ),
            vec![SequenceItem::section(
                "Sign in to the account that this agent will use for 'github'",
                ["https://github.com/login/oauth/authorize?state=session"],
            )]
        );
    }

    #[test]
    fn authorized_mcp_create_reports_one_complete_sequence() {
        assert_eq!(
            mcp_oauth_create_result_items(
                "notion",
                "https://mcp.notion.com",
                Some("https://api.notion.com/v1/oauth/authorize?state=session"),
            ),
            vec![SequenceItem::section(
                "Sign in to the account that this agent will use for 'notion'",
                ["https://api.notion.com/v1/oauth/authorize?state=session"],
            )]
        );
    }

    #[test]
    fn poll_oauth_authorization_waits_until_completed() {
        let mut statuses = ["pending", "completed"].into_iter();

        let result =
            poll_oauth_authorization("pending", || Ok(statuses.next().unwrap().to_string()));

        assert!(result.is_ok());
    }

    #[test]
    fn poll_oauth_authorization_reports_failed_session() {
        let error = poll_oauth_authorization("pending", || Ok("failed".to_string()))
            .unwrap_err()
            .to_string();

        assert!(error.contains("sign-in failed"), "{error}");
    }

    #[test]
    fn poll_oauth_authorization_reports_expired_session() {
        let error = poll_oauth_authorization("expired", || {
            panic!("an expired session must not be polled")
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("sign-in expired"), "{error}");
    }

    #[test]
    fn oauth_status_wait_stops_when_ctrl_c_is_requested() {
        let (_sender, receiver) = std::sync::mpsc::channel::<Result<String>>();

        let error =
            receive_oauth_status(&receiver, &crate::interrupt::Interrupt::requested()).unwrap_err();

        assert!(crate::error::is_cancellation(&error), "{error:#}");
    }

    #[test]
    fn completed_authorization_reports_agent_account() {
        assert_eq!(
            authorization_completed_items("my-notion"),
            [SequenceItem::section(
                "Agent account connected",
                ["my-notion"],
            )]
        );
    }

    #[test]
    fn client_credentials_success_reports_grant_and_scopes() {
        assert_eq!(
            client_credentials_created_items(
                "spotify",
                OAuthGrantType::ClientCredentials,
                &["playlist-read".to_string()],
            ),
            [SequenceItem::section(
                "Agent OAuth connection created",
                [
                    "spotify",
                    "Grant type: client_credentials",
                    "Scopes: playlist-read",
                ],
            )]
        );
    }

    #[test]
    fn client_credentials_rejects_a_non_completed_exchange() {
        let session = langsmith::OAuthAuthorizationSession {
            id: "session-id".to_string(),
            status: "pending".to_string(),
            verification_url: "https://example.com/authorize".to_string(),
            error_code: None,
        };

        let error = require_completed_client_credentials_session(&session)
            .unwrap_err()
            .to_string();

        assert!(error.contains("returned status 'pending'"), "{error}");
    }

    #[test]
    fn client_credentials_non_completed_exchange_rolls_back() {
        let deleted = Cell::new(false);
        let session = langsmith::OAuthAuthorizationSession {
            id: "session-id".to_string(),
            status: "failed".to_string(),
            verification_url: String::new(),
            error_code: None,
        };

        let result = rollback_on_authorization_failure(
            require_completed_client_credentials_session(&session),
            || {
                deleted.set(true);
                Ok(true)
            },
            "spotify",
        );

        assert!(result.is_err());
        assert!(deleted.get());
    }

    #[test]
    fn browser_open_prompt_says_to_press_enter() {
        let theme = BrowserOpenPromptTheme;
        let rendered = cliclack::Theme::format_header(
            &theme,
            &cliclack::ThemeState::Active,
            OPEN_BROWSER_PROMPT,
        );

        assert_eq!(
            console::strip_ansi_codes(&rendered),
            "◆  Press Enter to open your browser\n"
        );
    }

    #[test]
    fn browser_open_prompt_submit_frame_is_empty() {
        let theme = BrowserOpenPromptTheme;
        let state = cliclack::ThemeState::Submit;
        let rendered = format!(
            "{}{}{}",
            cliclack::Theme::format_header(&theme, &state, OPEN_BROWSER_PROMPT),
            cliclack::Theme::format_select_item(&theme, &state, true, "", ""),
            cliclack::Theme::format_footer_with_message(&theme, &state, ""),
        );

        assert!(rendered.is_empty(), "{rendered:?}");
    }

    #[test]
    fn interactive_authorization_confirms_before_opening_browser() {
        let events = RefCell::new(Vec::new());

        confirm_and_open_browser(
            true,
            "https://accounts.example.com/authorize",
            || {
                events.borrow_mut().push("confirm");
                Ok(())
            },
            |_| events.borrow_mut().push("open"),
        )
        .unwrap();

        assert_eq!(*events.borrow(), ["confirm", "open"]);
    }

    #[test]
    fn authorize_preserves_a_user_owned_manifest() {
        assert_eq!(
            resolve_owner_from_manifest_and_kind(
                Some(ConnectionOwnerPolicy::User),
                true,
                true,
                OAuthGrantType::AuthorizationCode,
            )
            .unwrap(),
            ConnectionOwnerPolicy::User
        );
    }

    #[test]
    fn oauth_create_lists_the_redirect_uri_before_credentials() {
        assert_eq!(
            oauth_redirect_uri_items(
                "https://dev.api.smith.langchain.com/v1/agent-auth/oauth/callback",
                Some("https://www.notion.so/my-integrations"),
            ),
            [crate::cli_output::SequenceItem::section(
                "Redirect URI",
                [
                    "https://dev.api.smith.langchain.com/v1/agent-auth/oauth/callback",
                    "",
                    "Register an app at https://www.notion.so/my-integrations",
                ],
            )]
        );
    }

    #[test]
    fn oauth_create_lists_the_redirect_uri_without_a_catalog_page() {
        assert_eq!(
            oauth_redirect_uri_items(
                "https://dev.api.smith.langchain.com/v1/agent-auth/oauth/callback",
                None,
            ),
            [crate::cli_output::SequenceItem::section(
                "Redirect URI",
                ["https://dev.api.smith.langchain.com/v1/agent-auth/oauth/callback"],
            )]
        );
    }

    #[test]
    fn oauth_registration_guidance_is_hidden_when_client_id_is_supplied() {
        assert!(!should_show_oauth_registration_guidance(Some("client-id")));
    }

    #[test]
    fn oauth_authorization_failure_rolls_back_the_new_connection() {
        let deleted = std::cell::Cell::new(false);

        let error = rollback_on_authorization_failure::<()>(
            Err(anyhow::anyhow!("session failed")),
            || {
                deleted.set(true);
                Ok(true)
            },
            "my-notion",
        )
        .unwrap_err();

        assert!(deleted.get());
        assert_eq!(error.to_string(), "session failed");
    }

    #[test]
    fn oauth_authorization_cancellation_rolls_back_the_new_connection() {
        let deleted = std::cell::Cell::new(false);
        let cancellation = anyhow::Error::new(io::Error::from(io::ErrorKind::Interrupted));

        let error = rollback_on_authorization_failure::<()>(
            Err(cancellation),
            || {
                deleted.set(true);
                Ok(true)
            },
            "my-notion",
        )
        .unwrap_err();

        assert!(deleted.get());
        assert!(crate::error::is_cancellation(&error), "{error:#}");
    }

    #[test]
    fn oauth_cancellation_reports_a_failed_cleanup() {
        let cancellation = anyhow::Error::new(io::Error::from(io::ErrorKind::Interrupted));

        let error = rollback_on_authorization_failure::<()>(
            Err(cancellation),
            || Err(anyhow::anyhow!("cleanup failed")),
            "my-notion",
        )
        .unwrap_err();

        assert!(!crate::error::is_cancellation(&error), "{error:#}");
        assert!(
            format!("{error:#}").contains("mda connections delete my-notion"),
            "{error:#}"
        );
    }

    #[test]
    fn agent_authorization_starts_for_a_managed_deployment() {
        let received_owner = std::cell::RefCell::new(None);

        let authorization = start_agent_oauth_authorization(
            Some("deployment-id"),
            |owner_id| {
                received_owner.replace(Some(owner_id.to_string()));
                Ok("authorization")
            },
            || panic!("successful authorization must not roll back"),
            "my-notion",
        )
        .unwrap();

        assert_eq!(
            (authorization, received_owner.into_inner()),
            (Some("authorization"), Some("deployment-id".to_string()))
        );
    }

    #[test]
    fn oauth_authorization_reports_a_failed_rollback() {
        let error = rollback_on_authorization_failure::<()>(
            Err(anyhow::anyhow!("session failed")),
            || Err(anyhow::anyhow!("cleanup failed")),
            "my-notion",
        )
        .unwrap_err();

        let message = format!("{error:#}");
        assert!(message.contains("session failed"), "{message}");
        assert!(message.contains("cleanup failed"), "{message}");
        assert!(
            message.contains("mda connections delete my-notion"),
            "{message}"
        );
    }

    #[test]
    fn oauth_create_result_reports_slug_redirect_uri_and_scopes() {
        assert_eq!(
            oauth_created_items(
                "my-x",
                "https://api.smith.langchain.com/cb",
                &["tweet.read".to_string(), "users.read".to_string()],
                Some("x"),
            ),
            [crate::cli_output::SequenceItem::section(
                "OAuth connection created",
                [
                    "my-x",
                    "Redirect URI: https://api.smith.langchain.com/cb",
                    "Scopes: tweet.read users.read",
                ],
            )]
        );
    }

    #[test]
    fn oauth_create_result_warns_when_a_manual_registration_names_no_scopes() {
        let items = oauth_created_items("my-x", "https://api.smith.langchain.com/cb", &[], None);

        assert_eq!(
            items[1],
            crate::cli_output::SequenceItem::section(
                "No scopes registered",
                [
                    "Connect will send an authorization request without a scope parameter.",
                    "Most providers reject that. Re-create with --scope, or with --oauth \
                     <service> when the catalog covers this provider.",
                ],
            )
        );
    }

    /// Notion registers no scopes by design, so the catalog path must stay quiet.
    #[test]
    fn oauth_create_result_does_not_warn_when_a_catalog_service_has_no_scopes() {
        let items = oauth_created_items(
            "my-notion",
            "https://api.smith.langchain.com/cb",
            &[],
            Some("notion-api"),
        );

        assert_eq!(items.len(), 1);
        assert_eq!(
            items[0],
            crate::cli_output::SequenceItem::section(
                "OAuth connection created",
                [
                    "my-notion",
                    "Redirect URI: https://api.smith.langchain.com/cb",
                    "Scopes: none",
                ],
            )
        );
    }

    #[test]
    fn oauth_create_requested_is_false_when_no_oauth_field_is_set() {
        assert!(!oauth_create_requested(&OAuthCreateOptions::default()));
    }

    #[test]
    fn oauth_create_requested_is_true_for_allowed_scopes_auth_method_and_params() {
        assert!(oauth_create_requested(&OAuthCreateOptions {
            grant_type: Some("authorization_code".into()),
            ..Default::default()
        }));
        assert!(oauth_create_requested(&OAuthCreateOptions {
            allowed_scopes: vec!["read".into()],
            ..Default::default()
        }));
        assert!(oauth_create_requested(&OAuthCreateOptions {
            auth_method: Some("client_secret_basic".into()),
            ..Default::default()
        }));
        assert!(oauth_create_requested(&OAuthCreateOptions {
            authorization_params: vec!["prompt=consent".into()],
            ..Default::default()
        }));
    }

    #[test]
    fn create_intent_uses_oauth_when_any_oauth_field_is_set() {
        let oauth = OAuthCreateOptions {
            allowed_scopes: vec!["read".into()],
            ..Default::default()
        };
        assert_eq!(
            create_intent(Some(&oauth), &ValueSource::Unspecified, true),
            CreateIntent::OAuth
        );
    }

    #[test]
    fn create_intent_uses_secret_for_env_file_or_piped_stdin() {
        assert_eq!(
            create_intent(None, &ValueSource::FromEnv("TOKEN".into()), false),
            CreateIntent::Secret
        );
        assert_eq!(
            create_intent(
                None,
                &ValueSource::FromFile(PathBuf::from("secret.txt")),
                false
            ),
            CreateIntent::Secret
        );
        assert_eq!(
            create_intent(None, &ValueSource::Unspecified, true),
            CreateIntent::Secret
        );
    }

    #[test]
    fn create_intent_infers_mcp_only_when_no_oauth_or_secret_input_exists() {
        assert_eq!(
            create_intent(None, &ValueSource::Unspecified, false),
            CreateIntent::InferMcp
        );
    }

    #[test]
    fn mcp_oauth_created_reports_connection_slug_and_url() {
        assert_eq!(
            mcp_oauth_created_items("my-notion", "https://mcp.notion.com/mcp"),
            [crate::cli_output::SequenceItem::section(
                "Automatically discovered and created MCP connection",
                [
                    "connection:   my-notion",
                    "url:          https://mcp.notion.com/mcp",
                ],
            )]
        );
    }

    #[test]
    fn mcp_url_without_a_scheme_gets_https() {
        assert_eq!(
            normalize_mcp_server_url("mcp.acme.com/mcp").unwrap(),
            "https://mcp.acme.com/mcp"
        );
    }

    #[test]
    fn mcp_https_url_is_kept() {
        assert_eq!(
            normalize_mcp_server_url("https://mcp.acme.com/mcp").unwrap(),
            "https://mcp.acme.com/mcp"
        );
    }

    #[test]
    fn mcp_url_trims_surrounding_space() {
        assert_eq!(
            normalize_mcp_server_url("  mcp.acme.com/mcp  ").unwrap(),
            "https://mcp.acme.com/mcp"
        );
    }

    #[test]
    fn mcp_url_keeps_the_path_and_query() {
        assert_eq!(
            normalize_mcp_server_url("https://mcp.acme.com/mcp/?resource=/").unwrap(),
            "https://mcp.acme.com/mcp/?resource=/"
        );
    }

    #[test]
    fn mcp_url_keeps_a_trailing_slash() {
        assert_eq!(
            normalize_mcp_server_url("https://mcp.acme.com/mcp/").unwrap(),
            "https://mcp.acme.com/mcp/"
        );
    }

    const SAAS_HOST: &str = "https://api.host.langchain.com";

    #[test]
    fn a_private_mcp_address_is_refused_for_a_hosted_langsmith() {
        for address in [
            "http://localhost:3000/mcp",
            "http://127.0.0.1:3000/mcp",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.1.2.3/mcp",
            "http://192.168.1.10/mcp",
            "http://172.16.0.9/mcp",
            "http://[::1]:3000/mcp",
            "http://[::ffff:127.0.0.1]/mcp",
            "http://[fd00::1]/mcp",
        ] {
            let error = reject_mcp_address_langsmith_cannot_reach(address, SAAS_HOST)
                .unwrap_err()
                .to_string();
            assert!(error.contains("public internet"), "{address}");
        }
    }

    /// A local backend can reach a local MCP server, so the same address has to
    /// keep working there.
    #[test]
    fn a_private_mcp_address_is_allowed_for_a_local_langsmith() {
        assert!(reject_mcp_address_langsmith_cannot_reach(
            "http://localhost:3000/mcp",
            "http://localhost:8300"
        )
        .is_ok());
    }

    #[test]
    fn a_public_mcp_address_is_allowed() {
        assert!(
            reject_mcp_address_langsmith_cannot_reach("https://mcp.acme.com/mcp", SAAS_HOST)
                .is_ok()
        );
    }

    /// The CLI cannot resolve names the way LangSmith does, so a name that is
    /// not an IP literal must not be guessed at.
    #[test]
    fn a_hostname_is_not_treated_as_private() {
        assert!(!is_private_address("https://internal.acme.com/mcp"));
    }

    #[test]
    fn mcp_url_rejects_a_non_http_scheme() {
        let error = normalize_mcp_server_url("ftp://mcp.acme.com/mcp")
            .unwrap_err()
            .to_string();
        assert!(error.contains("http or https"), "{error}");
    }

    #[test]
    fn mcp_url_rejects_an_empty_value() {
        let error = normalize_mcp_server_url("   ").unwrap_err().to_string();
        assert!(error.contains("--mcp needs"), "{error}");
    }

    #[test]
    fn mcp_create_rejects_oauth_flags() {
        let error = create(CreateOptions {
            target: ConnectionTarget {
                path: PathBuf::from("."),
                workspace_id: None,
                host_url: None,
            },
            slug: "foobar".into(),
            value: ValueSource::Unspecified,
            authorize: false,
            oauth: Some(OAuthCreateOptions {
                service: Some("github".into()),
                ..Default::default()
            }),
            mcp: Some("mcp.acme.com/mcp".into()),
        })
        .unwrap_err()
        .to_string();
        assert!(error.contains("--mcp cannot be combined"), "{error}");
    }

    #[test]
    fn a_reachable_server_without_dcr_names_the_url_and_the_fallback() {
        let message =
            mcp_discovery_error(McpProbe::Responded, "foobar", "https://mcp.acme.com/mcp")
                .to_string();

        assert!(message.contains("https://mcp.acme.com/mcp"), "{message}");
        assert!(
            message.contains("does not support CIMD or OAuth Dynamic Client Registration"),
            "{message}"
        );
        assert!(message.contains("--oauth"), "{message}");
        assert!(message.contains("--client-id"), "{message}");
    }

    /// A typo'd host is the common case, and it must not read as a provider
    /// that refused to register a client.
    #[test]
    fn an_unreachable_server_is_not_reported_as_missing_dcr() {
        let error = mcp_discovery_error(
            McpProbe::Unreachable,
            "my-2linear",
            "https://mscp.linear.app/mcp",
        );

        let message = error.to_string();
        assert_eq!(category_of(&error), ErrorCategory::Network);
        assert!(message.contains("did not answer"), "{message}");
        assert!(message.contains("typo"), "{message}");
        assert!(
            !message.contains("does not support CIMD or OAuth Dynamic Client Registration"),
            "{message}"
        );
    }

    #[test]
    fn a_server_answering_404_points_at_the_path() {
        let error = mcp_discovery_error(McpProbe::NotFound, "foobar", "https://mcp.acme.com/mpc");

        let message = error.to_string();
        assert_eq!(category_of(&error), ErrorCategory::UserInput);
        assert!(message.contains("answered 404"), "{message}");
        assert!(message.contains("path"), "{message}");
    }

    #[test]
    fn a_missing_probe_status_is_unreachable() {
        assert_eq!(classify_mcp_probe(None), McpProbe::Unreachable);
    }

    #[test]
    fn a_404_probe_status_is_not_found() {
        assert_eq!(
            classify_mcp_probe(Some(StatusCode::NOT_FOUND)),
            McpProbe::NotFound
        );
    }

    #[test]
    fn any_other_probe_status_is_responded() {
        assert_eq!(
            classify_mcp_probe(Some(StatusCode::UNAUTHORIZED)),
            McpProbe::Responded
        );
    }

    #[test]
    fn mcp_dcr_conflict_still_names_the_existing_slug() {
        let message = explain_mcp_dcr(
            anyhow::Error::new(ApiError {
                status: StatusCode::CONFLICT,
                detail: "already exists".into(),
            }),
            "foobar",
            "https://mcp.acme.com/mcp",
        )
        .to_string();
        assert!(message.contains("already exists"), "{message}");
        assert!(
            !message.contains("Dynamic Client Registration"),
            "{message}"
        );
    }

    #[test]
    fn agent_auth_cimd_failure_is_treated_as_unsupported_registration() {
        assert!(dcr_unsupported_detail(
            "authorization server supports neither CIMD nor dynamic client registration"
        ));
    }

    #[test]
    fn selecting_a_unique_user_owned_mcp_connection_returns_it() {
        let selected = select_user_owned_mcp_connection(
            vec![
                compile::McpConnectionEntry {
                    connector_name: "mcp".to_string(),
                    server_name: "notion".to_string(),
                    url: "https://mcp.notion.com/mcp".to_string(),
                    slug: "my-notion".to_string(),
                    owner_policy: "user".to_string(),
                },
                compile::McpConnectionEntry {
                    connector_name: "mcp".to_string(),
                    server_name: "docs".to_string(),
                    url: "https://docs.langchain.com/mcp".to_string(),
                    slug: "docs-token".to_string(),
                    owner_policy: "agent".to_string(),
                },
            ],
            "my-notion",
        )
        .unwrap();

        assert_eq!(
            selected.unwrap(),
            compile::McpConnectionEntry {
                connector_name: "mcp".to_string(),
                server_name: "notion".to_string(),
                url: "https://mcp.notion.com/mcp".to_string(),
                slug: "my-notion".to_string(),
                owner_policy: "user".to_string(),
            }
        );
    }

    #[test]
    fn selecting_an_agent_owned_mcp_connection_returns_none() {
        let selected = select_user_owned_mcp_connection(
            vec![compile::McpConnectionEntry {
                connector_name: "mcp".to_string(),
                server_name: "x".to_string(),
                url: "https://api.x.com/mcp".to_string(),
                slug: "x-api".to_string(),
                owner_policy: "agent".to_string(),
            }],
            "x-api",
        )
        .unwrap();

        assert!(selected.is_none());
    }

    #[test]
    fn selecting_multiple_user_owned_mcp_connections_is_rejected() {
        let error = select_user_owned_mcp_connection(
            vec![
                compile::McpConnectionEntry {
                    connector_name: "first".to_string(),
                    server_name: "notion".to_string(),
                    url: "https://mcp.notion.com/mcp".to_string(),
                    slug: "shared".to_string(),
                    owner_policy: "user".to_string(),
                },
                compile::McpConnectionEntry {
                    connector_name: "second".to_string(),
                    server_name: "linear".to_string(),
                    url: "https://mcp.linear.app/mcp".to_string(),
                    slug: "shared".to_string(),
                    owner_policy: "user".to_string(),
                },
            ],
            "shared",
        )
        .unwrap_err()
        .to_string();

        assert!(error.contains("multiple user-owned MCP servers"), "{error}");
        assert!(error.contains("first::notion"), "{error}");
        assert!(error.contains("second::linear"), "{error}");
    }

    #[test]
    fn agent_create_result_reports_slug_and_source_on_separate_lines() {
        assert_eq!(
            agent_created_items("openai-key", "OPENAI_API_KEY in the shell"),
            [crate::cli_output::SequenceItem::section(
                "Agent connection created",
                ["openai-key", "Source: OPENAI_API_KEY in the shell"],
            )]
        );
    }

    #[test]
    fn empty_workspace_result_is_complete() {
        assert_eq!(
            no_connections_found_items(),
            [crate::cli_output::SequenceItem::section(
                "No connections found",
                ["This workspace has no connections."],
            )]
        );
    }

    #[test]
    fn delete_results_report_the_final_state() {
        assert_eq!(
            connection_deleted_items("my-notion", true),
            [crate::cli_output::SequenceItem::section(
                "Connection deleted",
                ["my-notion"],
            )]
        );
        assert_eq!(
            connection_deleted_items("my-notion", false),
            [crate::cli_output::SequenceItem::section(
                "Connection already absent",
                ["my-notion"],
            )]
        );
        assert_eq!(
            connection_kept_items("my-notion"),
            [crate::cli_output::SequenceItem::section(
                "Connection kept",
                ["my-notion"],
            )]
        );
    }

    #[test]
    fn agent_owned_oauth_manifest_is_rejected() {
        let error = resolve_owner_from_manifest_and_kind(
            Some(ConnectionOwnerPolicy::Agent),
            true,
            false,
            OAuthGrantType::AuthorizationCode,
        )
        .unwrap_err()
        .to_string();

        assert!(
            error.contains("OAuth connections are user-owned"),
            "{error}"
        );
    }

    #[test]
    fn client_credentials_defaults_to_agent_ownership() {
        assert_eq!(
            resolve_owner_from_manifest_and_kind(
                None,
                true,
                false,
                OAuthGrantType::ClientCredentials,
            )
            .unwrap(),
            ConnectionOwnerPolicy::Agent
        );
    }

    #[test]
    fn client_credentials_rejects_user_owned_manifest() {
        let error = resolve_owner_from_manifest_and_kind(
            Some(ConnectionOwnerPolicy::User),
            true,
            false,
            OAuthGrantType::ClientCredentials,
        )
        .unwrap_err()
        .to_string();

        assert!(error.contains("agent-owned"), "{error}");
    }

    #[test]
    fn client_credentials_rejects_authorization_flow_flags() {
        let oauth = OAuthCreateOptions {
            grant_type: Some("client_credentials".into()),
            authorize_url: Some("https://example.com/authorize".into()),
            ..Default::default()
        };

        let error = validate_client_credentials_flags(&oauth)
            .unwrap_err()
            .to_string();

        assert!(error.contains("--authorize-url"), "{error}");
    }

    #[test]
    fn client_credentials_rejects_allowed_scope_override() {
        let oauth = OAuthCreateOptions {
            grant_type: Some("client_credentials".into()),
            allowed_scopes: vec!["read".into()],
            ..Default::default()
        };

        let error = validate_client_credentials_flags(&oauth)
            .unwrap_err()
            .to_string();

        assert!(error.contains("--allowed-scope"), "{error}");
    }

    #[test]
    fn client_credentials_accepts_default_flags() {
        assert!(validate_client_credentials_flags(&OAuthCreateOptions::default()).is_ok());
    }

    #[test]
    fn user_owned_opaque_manifest_is_rejected() {
        let error = resolve_owner_from_manifest_and_kind(
            Some(ConnectionOwnerPolicy::User),
            false,
            false,
            OAuthGrantType::AuthorizationCode,
        )
        .unwrap_err()
        .to_string();

        assert!(error.contains("Fixed secrets are agent-owned"), "{error}");
    }

    #[test]
    fn opaque_create_without_manifest_defaults_to_agent() {
        assert_eq!(
            resolve_owner_from_manifest_and_kind(
                None,
                false,
                false,
                OAuthGrantType::AuthorizationCode,
            )
            .unwrap(),
            ConnectionOwnerPolicy::Agent
        );
    }

    #[test]
    fn authorize_requires_an_oauth_connection() {
        let error = create(CreateOptions {
            target: ConnectionTarget {
                path: PathBuf::from("."),
                workspace_id: None,
                host_url: None,
            },
            slug: "openai".into(),
            value: ValueSource::Unspecified,
            authorize: true,
            oauth: None,
            mcp: None,
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("--authorize requires OAuth"), "{error}");
    }

    #[test]
    fn oauth_create_without_a_client_id_rejects_a_value_source() {
        let error = create_oauth(
            CreateOptions {
                target: ConnectionTarget {
                    path: PathBuf::from("."),
                    workspace_id: None,
                    host_url: None,
                },
                slug: "gh".into(),
                value: ValueSource::FromEnv("GH_SECRET".into()),
                authorize: false,
                oauth: Some(OAuthCreateOptions {
                    service: Some("github".into()),
                    ..Default::default()
                }),
                mcp: None,
            },
            ConnectionOwnerPolicy::Agent,
        )
        .unwrap_err()
        .to_string();

        assert!(error.contains("--client-id"), "{error}");
    }

    #[test]
    fn catalog_endpoints_fill_in_for_a_known_service() {
        let oauth = OAuthCreateOptions {
            service: Some("notion-api".into()),
            client_id: Some("client-id".into()),
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(
            resolved.authorize_url.as_deref(),
            Some("https://api.notion.com/v1/oauth/authorize")
        );
        assert_eq!(
            resolved.token_url.as_deref(),
            Some("https://api.notion.com/v1/oauth/token")
        );
        assert_eq!(resolved.token_endpoint_auth_method, "client_secret_basic");
        assert_eq!(resolved.authorization_params["owner"], "user");
    }

    #[test]
    fn authorization_code_is_the_default_grant_type() {
        assert_eq!(
            resolve_oauth_grant_type(&OAuthCreateOptions::default()).unwrap(),
            OAuthGrantType::AuthorizationCode
        );
    }

    #[test]
    fn client_credentials_catalog_resolution_omits_authorization_defaults() {
        let oauth = OAuthCreateOptions {
            service: Some("spotify".into()),
            grant_type: Some("client_credentials".into()),
            client_id: Some("client-id".into()),
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert!(resolved.authorize_url.is_none());
        assert!(resolved.authorization_params.is_empty());
        assert!(resolved.scopes.is_empty());
        assert_eq!(
            resolved.token_url.as_deref(),
            Some("https://accounts.spotify.com/api/token")
        );
    }

    #[test]
    fn client_credentials_keeps_explicit_scopes() {
        let oauth = OAuthCreateOptions {
            service: Some("spotify".into()),
            grant_type: Some("client_credentials".into()),
            scopes: vec!["playlist-read".into()],
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(resolved.scopes, ["playlist-read"]);
        assert_eq!(resolved.allowed_scopes, ["playlist-read"]);
    }

    #[test]
    fn an_unknown_service_names_the_ones_that_are_known() {
        let oauth = OAuthCreateOptions {
            service: Some("nope".into()),
            client_id: Some("client-id".into()),
            ..Default::default()
        };

        let error = resolve_oauth_endpoints(&oauth).unwrap_err().to_string();

        assert!(error.contains("not a known OAuth service"), "{error}");
        assert!(error.contains("notion-api"), "{error}");
        assert!(error.contains("--authorize-url"), "{error}");
    }

    #[test]
    fn manual_urls_override_only_the_urls() {
        let oauth = OAuthCreateOptions {
            service: Some("notion-api".into()),
            client_id: Some("client-id".into()),
            authorize_url: Some("https://auth.acme.com/authorize".into()),
            token_url: Some("https://auth.acme.com/token".into()),
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(
            resolved.authorize_url.as_deref(),
            Some("https://auth.acme.com/authorize")
        );
        assert_eq!(resolved.token_endpoint_auth_method, "client_secret_basic");
        assert_eq!(resolved.authorization_params["owner"], "user");
    }

    #[test]
    fn requested_scopes_replace_the_catalog_defaults() {
        let defaults = OAuthCreateOptions {
            service: Some("google".into()),
            client_id: Some("client-id".into()),
            ..Default::default()
        };
        assert!(resolve_oauth_endpoints(&defaults)
            .unwrap()
            .scopes
            .contains(&"openid".to_string()));

        let narrowed = OAuthCreateOptions {
            scopes: vec!["https://www.googleapis.com/auth/drive.readonly".to_string()],
            ..defaults
        };

        let resolved = resolve_oauth_endpoints(&narrowed).unwrap();

        assert_eq!(
            resolved.scopes,
            ["https://www.googleapis.com/auth/drive.readonly"]
        );
    }

    #[test]
    fn an_unknown_service_is_rejected_even_with_a_manual_url() {
        let oauth = OAuthCreateOptions {
            service: Some("nope".into()),
            client_id: Some("client".into()),
            authorize_url: Some("https://example.com/authorize".into()),
            ..Default::default()
        };

        let error = resolve_oauth_endpoints(&oauth).unwrap_err().to_string();

        assert!(error.contains("not a known OAuth service"), "{error}");
    }

    #[test]
    fn a_service_name_resolves_to_its_canonical_spelling() {
        let oauth = OAuthCreateOptions {
            service: Some("Notion-API".into()),
            client_id: Some("client-id".into()),
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(resolved.service, Some("notion-api"));
    }

    #[test]
    fn an_auth_method_flag_overrides_the_catalog_entry() {
        let oauth = OAuthCreateOptions {
            service: Some("notion-api".into()),
            client_id: Some("client-id".into()),
            ..Default::default()
        };
        assert_eq!(
            resolve_oauth_endpoints(&oauth)
                .unwrap()
                .token_endpoint_auth_method,
            "client_secret_basic"
        );

        let overridden = OAuthCreateOptions {
            auth_method: Some("client_secret_post".into()),
            ..oauth
        };

        assert_eq!(
            resolve_oauth_endpoints(&overridden)
                .unwrap()
                .token_endpoint_auth_method,
            "client_secret_post"
        );
    }

    #[test]
    fn a_manual_provider_defaults_to_basic_rather_than_inferring_one() {
        let oauth = OAuthCreateOptions {
            client_id: Some("client-id".into()),
            authorize_url: Some("https://auth.acme.com/authorize".into()),
            token_url: Some("https://auth.acme.com/token".into()),
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(resolved.token_endpoint_auth_method, "client_secret_basic");
        assert!(!resolved.is_public_client());
    }

    #[test]
    fn allowed_scopes_default_to_the_requested_ones() {
        let oauth = OAuthCreateOptions {
            service: Some("github".into()),
            client_id: Some("client-id".into()),
            scopes: vec!["repo".to_string()],
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(resolved.scopes, ["repo"]);
        assert_eq!(resolved.allowed_scopes, ["repo"]);
    }

    #[test]
    fn a_requested_scope_outside_the_ceiling_is_rejected() {
        let oauth = OAuthCreateOptions {
            service: Some("github".into()),
            client_id: Some("client-id".into()),
            scopes: vec!["repo".to_string()],
            allowed_scopes: vec!["read:user".to_string()],
            ..Default::default()
        };

        let error = resolve_oauth_endpoints(&oauth).unwrap_err().to_string();

        assert!(error.contains("repo"), "{error}");
        assert!(error.contains("--allowed-scope"), "{error}");
    }

    #[test]
    fn authorization_params_merge_over_the_catalog_with_the_flag_winning() {
        let oauth = OAuthCreateOptions {
            service: Some("google".into()),
            client_id: Some("client-id".into()),
            authorization_params: vec![
                "prompt=select_account".to_string(),
                "login_hint=a@b.com".to_string(),
            ],
            ..Default::default()
        };

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert_eq!(resolved.authorization_params["access_type"], "offline");
        assert_eq!(resolved.authorization_params["prompt"], "select_account");
        assert_eq!(resolved.authorization_params["login_hint"], "a@b.com");
    }

    #[test]
    fn an_authorization_param_needs_a_key_and_an_equals() {
        let error = parse_authorization_params(&["nope".to_string()])
            .unwrap_err()
            .to_string();
        assert!(error.contains("KEY=VALUE"), "{error}");

        let error = parse_authorization_params(&["=value".to_string()])
            .unwrap_err()
            .to_string();
        assert!(error.contains("needs a key"), "{error}");

        let parsed = parse_authorization_params(&["a=b=c".to_string()]).unwrap();
        assert_eq!(parsed["a"], "b=c");
    }

    #[test]
    fn token_params_reject_reserved_keys_case_insensitively() {
        let error = parse_token_params(&["Client_Secret=value".to_string()])
            .unwrap_err()
            .to_string();

        assert!(error.contains("reserved OAuth parameter"), "{error}");
        assert!(!error.contains("value"), "{error}");
    }

    #[test]
    fn repeated_token_params_use_the_last_value() {
        let params =
            parse_token_params(&["audience=first".to_string(), "audience=second".to_string()])
                .unwrap();

        assert_eq!(params["audience"], "second");
    }

    #[test]
    fn token_request_headers_parse_key_value_pairs() {
        let headers = parse_token_request_headers(&[
            "Authorization=Bearer publishable-key".to_string(),
            "X-Provider-Version=2024-01".to_string(),
        ])
        .unwrap();

        assert_eq!(headers["Authorization"], "Bearer publishable-key");
        assert_eq!(headers["X-Provider-Version"], "2024-01");
    }

    #[test]
    fn token_request_header_errors_never_include_input() {
        for (pairs, expected) in [
            (
                vec![
                    "Authorization: Bearer 'fake-secret'",
                    "Authorization: Bearer fake-secret",
                    "Bearer fake-secret",
                    "fake-secret\r\n'quoted'",
                    "",
                ],
                "--token-request-header requires KEY=VALUE",
            ),
            (
                vec![
                    "=Bearer fake-secret",
                    "   =Bearer fake-secret'suffix",
                    "='fake=secret'",
                ],
                "--token-request-header requires a non-empty header name",
            ),
            (
                vec![
                    "Host=fake-secret",
                    "CONTENT-TYPE='fake-secret'",
                    "content-length=123",
                ],
                "--token-request-header contains a reserved header",
            ),
        ] {
            for pair in pairs {
                let error = parse_token_request_headers(&[
                    "X-Api-Key=another-fake-secret".into(),
                    pair.into(),
                ])
                .unwrap_err();

                assert!(!format!("{error:#}").contains("fake-secret"));
                assert_eq!(format!("{error:#}"), expected);
                assert_eq!(category_of(&error), ErrorCategory::UserInput);
            }
        }
    }

    #[test]
    fn token_request_headers_preserve_values_and_last_duplicate() {
        let headers = parse_token_request_headers(&[
            "X-Api-Key=first".into(),
            " X-Api-Key = 'fake=secret' ".into(),
            "X-Empty=".into(),
        ])
        .unwrap();

        assert_eq!(headers["X-Api-Key"], " 'fake=secret' ");
        assert_eq!(headers["X-Empty"], "");
    }

    #[test]
    fn no_service_and_no_manual_urls_resolves_no_endpoints() {
        let oauth = OAuthCreateOptions::default();

        let resolved = resolve_oauth_endpoints(&oauth).unwrap();

        assert!(resolved.authorize_url.is_none());
        assert!(resolved.token_url.is_none());
        assert!(resolved.scopes.is_empty());
    }

    #[test]
    fn the_table_lists_slug_kind_app_and_timestamps() {
        let connections = [
            summary("openai-key", "secret", "agent"),
            summary("notion", "oauth2", "user"),
        ];
        let mut out = Vec::new();
        write_connections(&mut out, &connections, false).unwrap();
        let rendered = String::from_utf8(out).unwrap();
        assert!(rendered.contains("SLUG"), "{rendered}");
        assert!(rendered.contains("KIND"), "{rendered}");
        assert!(rendered.contains("APP"), "{rendered}");
        assert!(rendered.contains("CREATED"), "{rendered}");
        assert!(rendered.contains("UPDATED"), "{rendered}");
        assert!(rendered.contains("openai-key"), "{rendered}");
        assert!(rendered.contains("notion"), "{rendered}");
        assert!(
            !rendered.contains("SERVICE"),
            "SERVICE is not returned by Agent Auth list: {rendered}"
        );
        assert!(
            !rendered.contains("OWNER"),
            "OWNER is not returned by Agent Auth list: {rendered}"
        );
    }

    #[test]
    fn json_output_is_always_an_array() {
        let mut out = Vec::new();
        write_connections(&mut out, &[], true).unwrap();
        assert_eq!(String::from_utf8(out).unwrap().trim(), "[]");
    }

    #[test]
    fn a_closed_pipe_is_success_for_both_formats() {
        for json in [true, false] {
            write_connections(
                &mut ClosedPipe,
                &[summary("openai-key", "secret", "agent")],
                json,
            )
            .unwrap_or_else(|err| panic!("json={json}: {err}"));
        }
    }

    #[test]
    fn delete_confirm_prompt_mentions_kind() {
        let oauth = summary("notion", "oauth2", "user");
        assert!(
            delete_confirm_prompt(&oauth).contains("OAuth"),
            "{}",
            delete_confirm_prompt(&oauth)
        );
        let secret = summary("openai-key", "secret", "agent");
        assert!(
            delete_confirm_prompt(&secret).contains("secret"),
            "{}",
            delete_confirm_prompt(&secret)
        );
        let other = summary("weird", "other", "agent");
        assert_eq!(delete_confirm_prompt(&other), "Delete connection 'weird'?");
    }
}
