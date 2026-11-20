//! A Fleet sandbox becomes `sandbox/`, and its access profiles become the
//! sandbox proxy's network list, headers and environment.
//!
//! A Fleet agent with a sandbox stores its settings on the agent
//! (`backend.sandbox_config`) and points at access profiles by id
//! (`policy_ids`). `fetch` downloads those profiles into the snapshot. Each
//! profile can hold:
//!
//! | Fleet profile field | Becomes in `sandbox/`                                   |
//! |---------------------|---------------------------------------------------------|
//! | `network`           | `proxy_config.access_control` allow/deny list           |
//! | `env_vars`          | values in `.env`, read by name into `run_config`        |
//! | `proxy_rules`       | `proxy_config.rules`, AWS and GCP, as written           |
//! | `rules`             | `proxy_config.rules`, one header rule each              |
//!
//! Nothing here blocks the export. A piece MDA can't express is left out of
//! `sandbox/` with a note under "Sandbox" in FLEET_EXPORT.md that says what to
//! do instead, and every connection a rule reads is listed under "Connections
//! to create" with the steps that create it.

use std::collections::BTreeSet;

use crate::connection::catalog;
use crate::fleet_export::report::ConnectionToCreate;
use crate::fleet_export::snapshot::{
    AccessProfile, AccessRule, Identity, Inject, RuleSource, SandboxConfig, WorkspaceConnection,
};
use crate::fleet_export::{Export, FleetAgent};
use crate::init::sandbox_env_key;
use crate::init::{
    AccessControl, Connection, ConnectionFormat, ConnectionOwner, HeaderValue, ProxyHeader,
    ProxyRule, SandboxOptions,
};
use crate::naming::canonical_name;
use crate::project::Language;

/// Fleet's GitHub provider signs requests in with Fleet's GitHub App, not an
/// OAuth connection.
const GITHUB_PROVIDER: &str = "github-oauth-provider";

/// Placeholders Fleet replaces with a rule's credential: `{access_token}` for
/// a connection, `{value}` for a secret, either for an Agent Auth connection.
const CREDENTIAL_PLACEHOLDERS: [&str; 2] = ["{access_token}", "{value}"];

/// The sandbox proxy matches this deny-list entry against every host. Fleet
/// stores `block_all` the same way.
const EVERY_HOST: &str = "*";

/// Picks what to write from the agent's `backend.type`:
///
/// | `backend.type`         | `sandbox/`                                     |
/// |------------------------|------------------------------------------------|
/// | missing, `""`, `state` | none: the agent has no computer                |
/// | `sandbox`              | mapped from `sandbox_config` and its profiles  |
/// | `computer_environment` | `mda init`'s default, listed as not exported   |
///
/// Computer environments (a sandbox setup saved for the whole workspace)
/// aren't mapped: almost no agent uses them.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    match agent.backend.as_ref() {
        None => out.blueprint.sandbox = false,
        Some(backend) => match backend.kind.as_str() {
            "" | "state" => out.blueprint.sandbox = false,
            "sandbox" => {
                let config = backend.sandbox_config.clone().unwrap_or_default();
                sandbox(agent, &config, out);
            }
            "computer_environment" => out.report.skip(
                "sandbox",
                "workspace computer environment",
                "this agent uses a workspace computer environment, which export doesn't support yet. The project has `mda init`'s standard sandbox instead.",
            ),
            kind => out.report.skip(
                "sandbox",
                format!("{kind} backend"),
                format!("this agent uses a `{kind}` sandbox, which export doesn't support yet. The project has `mda init`'s standard sandbox instead."),
            ),
        },
    }
}

/// Fills `blueprint.sandbox_options` from a `sandbox` backend, in four steps:
///
/// 1. Copy the sandbox's own settings (snapshot and TTLs), and note when
///    Fleet shared one sandbox across threads.
/// 2. For each profile in `policy_ids`, in order, merge its env vars and
///    network list, copy its AWS and GCP rules, and turn each of its rules
///    into a header rule (see [`header_rule`]), reporting the connections
///    those read.
/// 3. Note which env vars went into `.env`, and any that clash with a key
///    `mda init` writes there.
/// 4. Leave `sandbox_options` unset when nothing differs from the starter, so
///    `sandbox/` is the file `mda init` writes.
fn sandbox(agent: &FleetAgent, config: &SandboxConfig, out: &mut Export) {
    let file = sandbox_file(out.blueprint.language);
    // MDA has no scope option: every thread gets its own sandbox.
    if config.scope == "agent" {
        out.report.sandbox(
            "In Fleet, every conversation with this agent shared one sandbox. In MDA each conversation gets its own sandbox, so files won't be shared across threads natively.",
        );
    }
    let mut options = SandboxOptions {
        snapshot_id: config.snapshot_id.clone().filter(|id| !id.is_empty()),
        idle_ttl_seconds: config.idle_ttl_seconds,
        delete_after_stop_seconds: config.delete_after_stop_seconds,
        ..SandboxOptions::default()
    };
    // A shared Fleet agent runs with one account for every caller, so its
    // connections are agent-owned; otherwise each caller signs in (user-owned).
    // Same rule as the tools mapper.
    let shared = agent
        .permissions
        .as_ref()
        .is_some_and(|permissions| permissions.identity == Identity::Shared);
    // Rule names are kept distinct so each rule in `sandbox/` can be told
    // apart, and each connection is reported once even when several rules
    // read it.
    let mut rule_names = BTreeSet::new();
    let mut reported = BTreeSet::new();
    for id in &config.policy_ids {
        let Some(profile) = agent
            .access_profiles
            .iter()
            .find(|profile| &profile.id == id)
        else {
            // Deleted since (Fleet allows that while agents still list it),
            // or not readable by the caller.
            out.report.sandbox(format!(
                "Couldn't read Fleet access profile `{id}` (it may have been deleted), so its settings couldn't be carried over to `{file}`. If you still need them, check the profile in Fleet and add them to `{file}`."
            ));
            continue;
        };
        // Fleet's runtime fails when two profiles set one env var to different
        // values, so a working agent has no conflict for this merge to hide.
        options.env_vars.extend(profile.env_vars.clone());
        network(profile, &mut options);
        options
            .provider_rules
            .extend(profile.proxy_rules.iter().cloned());
        for rule in &profile.rules {
            let Some((rule, need)) = header_rule(agent, rule, shared, &mut rule_names, out) else {
                continue;
            };
            if let Some(need) = need {
                report_connection(agent, &rule, need, &mut reported, out);
            }
            options.rules.push(rule);
        }
    }
    if !options.env_vars.is_empty() {
        let keys: Vec<String> = options
            .env_vars
            .keys()
            .map(|name| format!("`{}`", sandbox_env_key(name)))
            .collect();
        out.report.sandbox(format!(
            "Copied Fleet's sandbox environment variables into `.env` as {}. `mda deploy` sends them to the deployment as secrets, and `{file}` passes each to the sandbox under its Fleet name.",
            keys.join(", ")
        ));
    }
    if options != SandboxOptions::default() {
        out.blueprint.sandbox_options = Some(options);
    }
}

/// Merges a profile's network rule into `options`. The sandbox proxy takes one
/// allow list or one deny list, not both:
///
/// | Profile's `network` | Already merged  | Result                          |
/// |---------------------|-----------------|---------------------------------|
/// | `allow`             | none or allow   | hosts added to the allow list   |
/// | `deny`              | none or deny    | hosts added to the deny list    |
/// | `block_all`         | none or deny    | `*` added to the deny list      |
/// | allow vs. deny      | the other kind  | every host blocked              |
fn network(profile: &AccessProfile, options: &mut SandboxOptions) {
    let Some(network) = &profile.network else {
        return;
    };
    let incoming = match network.kind.as_str() {
        "allow" => AccessControl::AllowList(network.hosts.clone()),
        "deny" => AccessControl::DenyList(network.hosts.clone()),
        "block_all" => AccessControl::DenyList(vec![EVERY_HOST.to_string()]),
        // Fleet's API accepts only the three types above.
        _ => return,
    };
    options.access_control = Some(match (options.access_control.take(), incoming) {
        (None, incoming) => incoming,
        (Some(AccessControl::AllowList(mut list)), AccessControl::AllowList(hosts)) => {
            list.extend(hosts);
            AccessControl::AllowList(list)
        }
        (Some(AccessControl::DenyList(mut list)), AccessControl::DenyList(hosts)) => {
            list.extend(hosts);
            AccessControl::DenyList(list)
        }
        // Fleet's runtime and the sandbox proxy both reject an allow list
        // mixed with a deny list, so no working agent gets here. Blocking
        // every host is the safe reading of a config that can't run.
        _ => AccessControl::DenyList(vec![EVERY_HOST.to_string()]),
    });
}

/// What a rule's connection needs before it works, for "Connections to create".
enum Need<'a> {
    /// An Agent Auth connection the workspace already has.
    Existing { connection: &'a WorkspaceConnection },
    /// An OAuth connection named after a Fleet provider.
    OAuth { source: &'a RuleSource },
    /// A secret connection holding a workspace secret's value.
    Secret { key: &'a str },
}

/// One Fleet rule as a proxy rule, plus what its connection needs. `None` when
/// the whole rule is left out or no header is left.
///
/// The rule's credential source decides where each header's value comes from:
///
/// | `source.type` | Fleet resolves it as                   | `sandbox/` reads it from                 |
/// |---------------|----------------------------------------|------------------------------------------|
/// | `secret`      | a workspace secret                     | the same secret (see [`secret_value`])   |
/// | `agent_auth`  | an Agent Auth connection, by id        | that connection's slug                   |
/// | `agent_auth`, shared agent, OAuth | the same connection | a new `<slug>-agent` connection   |
/// | `connection`  | the caller's OAuth token from provider | a connection named after the provider    |
/// | `connection` with Fleet's GitHub provider | Fleet's GitHub App | nothing: left out, with a note   |
///
/// Each header is then written in a format MDA supports (see
/// [`connection_format`]) or left out with a note. A rule whose connection is
/// agent-owned is written commented out until that connection exists.
fn header_rule<'a>(
    agent: &'a FleetAgent,
    rule: &'a AccessRule,
    shared: bool,
    names: &mut BTreeSet<String>,
    out: &mut Export,
) -> Option<(ProxyRule, Option<Need<'a>>)> {
    let file = sandbox_file(out.blueprint.language);
    let hosts = rule.match_hosts.join(", ");
    let source = &rule.source;
    let owner = if shared {
        ConnectionOwner::Agent
    } else {
        ConnectionOwner::User
    };
    // The connection a header reads, when it reads one.
    let connection = match source.kind.as_str() {
        "secret" => None,
        // MDA connections are Agent Auth connections too
        // (`/v1/agent-auth/connections`), so the id leads straight to a slug.
        "agent_auth" => {
            let Some(found) = agent
                .workspace_connections
                .iter()
                .find(|connection| connection.id == source.connection_id)
            else {
                out.report.sandbox(format!(
                    "Requests to {hosts} won't be signed in. The connection Fleet used for them (`{}`) isn't in this workspace anymore. Create a connection and add a rule for {hosts} to `{file}`.",
                    source.connection_id
                ));
                return None;
            };
            // `--authorize` signs a connection in for a deployment only while
            // creating it, and the workspace's slug is taken, so a shared
            // agent's OAuth rule reads a new connection.
            let name = if shared && found.kind == "oauth2" {
                slug(&format!("{}-agent", found.slug))
            } else {
                found.slug.clone()
            };
            Some((name, owner, Need::Existing { connection: found }))
        }
        "connection" if source.provider_id == GITHUB_PROVIDER => {
            out.report.sandbox(format!(
                "Requests to {hosts} won't be signed in to GitHub. Fleet used its own GitHub App for this. To set it up in MDA, run `mda channels init github` and then `mda channels setup`, which creates a GitHub App for this agent. Then add a rule for {hosts} to `{file}` that uses `connections.get(\"mda/github-token\", {{\"type\": \"agent\"}})`."
            ));
            return None;
        }
        // Provider ids look like `google-oauth-provider`; the slug drops the
        // suffix. Only GitHub's id is confirmed to have this shape.
        "connection" => Some((
            slug(
                source
                    .provider_id
                    .strip_suffix("-oauth-provider")
                    .unwrap_or(&source.provider_id),
            ),
            owner,
            Need::OAuth { source },
        )),
        // Fleet's API accepts only `secret`, `connection` and `agent_auth`,
        // all handled above. This covers a type Fleet adds later.
        kind => {
            out.report.sandbox(format!(
                "Requests to {hosts} won't be signed in. Export doesn't support Fleet's `{kind}` credential type yet."
            ));
            return None;
        }
    };
    let mut headers = Vec::new();
    let mut need = None;
    for inject in &rule.inject {
        let value = match &connection {
            None => match secret_value(inject, &source.key) {
                SecretValue::Template(template) => Some(HeaderValue::WorkspaceSecret(template)),
                // The proxy can't Basic-encode a workspace secret, but MDA can
                // encode a connection: hold the secret's value in an
                // agent-owned secret connection and wrap it in `basic()`.
                SecretValue::Basic(username) => {
                    need = Some(Need::Secret { key: &source.key });
                    Some(HeaderValue::Connection {
                        connection: Connection {
                            slug: slug(&source.key),
                            owner: ConnectionOwner::Agent,
                        },
                        format: ConnectionFormat::Basic { username },
                    })
                }
                SecretValue::Unsupported => None,
            },
            Some((slug, owner, _)) => {
                connection_format(inject).map(|format| HeaderValue::Connection {
                    connection: Connection {
                        slug: slug.clone(),
                        owner: *owner,
                    },
                    format,
                })
            }
        };
        match value {
            Some(value) => headers.push(ProxyHeader {
                name: inject.header.clone(),
                value,
            }),
            None => {
                let reason = if inject.value.kind == "http_basic" {
                    "MDA sends Basic auth only with a username that is set, has no colon, and isn't the credential itself, and Fleet's username doesn't fit"
                } else {
                    "it doesn't include the credential, so MDA has nothing to fill in"
                };
                out.report.sandbox(format!(
                    "The `{}` header Fleet added to requests for {hosts} wasn't carried over: {reason}. If {hosts} needs it, add the header to the rule in `{file}` yourself.",
                    inject.header
                ));
            }
        }
    }
    if headers.is_empty() {
        return None;
    }
    if let Some((_, _, connection_need)) = connection {
        need = Some(connection_need);
    }
    // Named after the first host, with `-2`, `-3`… when another rule took it.
    let base = slug(rule.match_hosts.first().map_or("rule", String::as_str));
    let mut name = base.clone();
    let mut n = 2;
    while !names.insert(name.clone()) {
        name = format!("{base}-{n}");
        n += 1;
    }
    // An agent-owned connection can't exist before the deployment does, and
    // a rule reading a missing connection fails every command, so the rule
    // waits commented out (same as agent-owned MCP servers).
    let agent_owned = headers.iter().find_map(|header| match &header.value {
        HeaderValue::Connection { connection, .. }
            if connection.owner == ConnectionOwner::Agent =>
        {
            Some(connection.slug.clone())
        }
        _ => None,
    });
    let commented_out = agent_owned.map(|slug| {
        format!(
            "After your first `mda deploy`, create the `{slug}` connection (see \"Connections to create\" in FLEET_EXPORT.md).\nThen uncomment this rule and deploy again."
        )
    });
    Some((
        ProxyRule {
            name,
            match_hosts: rule.match_hosts.clone(),
            headers,
            commented_out,
        },
        need,
    ))
}

/// A workspace secret's header value.
enum SecretValue {
    /// A template the proxy fills itself, with `{SECRET_NAME}`.
    Template(String),
    /// Basic auth with this literal username and the secret as the password.
    Basic(String),
    Unsupported,
}

/// Fleet's template names the credential `{value}`; the sandbox proxy names
/// it `{SECRET_NAME}`, so `Bearer {value}` becomes `Bearer {ACME_KEY}`.
/// `http_basic` needs base64 encoding, which the proxy doesn't do for secrets,
/// so it goes through a connection instead (see [`header_rule`]).
fn secret_value(inject: &Inject, key: &str) -> SecretValue {
    let value = &inject.value;
    match value.kind.as_str() {
        "template" => {
            SecretValue::Template(value.template.replace("{value}", &format!("{{{key}}}")))
        }
        "http_basic" if literal_username(&value.username) && value.password == "{value}" => {
            SecretValue::Basic(value.username.clone())
        }
        _ => SecretValue::Unsupported,
    }
}

/// How a connection's credential fits Fleet's header value, or `None` when MDA
/// can't write it:
///
/// | Fleet value                                    | Format     |
/// |------------------------------------------------|------------|
/// | `{access_token}` or `{value}`                  | `Raw`      |
/// | `Bearer {access_token}` or `Bearer {value}`    | `Bearer`   |
/// | any other text around the placeholder          | `Template` |
/// | `http_basic`, literal username, `{…}` password | `Basic`    |
/// | `http_basic` with the placeholder elsewhere    | `None`     |
///
/// `Template` is `credential_template` / `credentialTemplate`, which fills in
/// `{token}`. Fleet's templates hold only its placeholders and literal text
/// (`fleet/sandboxpolicies/validation.go`), so every one fits.
fn connection_format(inject: &Inject) -> Option<ConnectionFormat> {
    let value = &inject.value;
    match value.kind.as_str() {
        "template" => {
            let template = value.template.trim();
            if CREDENTIAL_PLACEHOLDERS.contains(&template) {
                return Some(ConnectionFormat::Raw);
            }
            if template
                .strip_prefix("Bearer ")
                .is_some_and(|rest| CREDENTIAL_PLACEHOLDERS.contains(&rest))
            {
                return Some(ConnectionFormat::Bearer);
            }
            let text = CREDENTIAL_PLACEHOLDERS
                .iter()
                .fold(value.template.clone(), |text, placeholder| {
                    text.replace(placeholder, "{token}")
                });
            // A header without the credential has nothing to fill in.
            text.contains("{token}")
                .then_some(ConnectionFormat::Template(text))
        }
        "http_basic"
            if literal_username(&value.username)
                && CREDENTIAL_PLACEHOLDERS.contains(&value.password.as_str()) =>
        {
            Some(ConnectionFormat::Basic {
                username: value.username.clone(),
            })
        }
        _ => None,
    }
}

/// `basic()` rejects an empty username or one with a colon, and a username
/// holding a placeholder would need the credential in it.
fn literal_username(username: &str) -> bool {
    !username.trim().is_empty() && !username.contains([':', '{'])
}

/// Lists the connection a rule reads under "Connections to create", or notes
/// that an existing one is reused:
///
/// | Owner | Connection                      | Reported                          |
/// |-------|---------------------------------|-----------------------------------|
/// | user  | already in the workspace        | console note: reused              |
/// | user  | OAuth, not in the workspace     | register an app, create it before deploying |
/// | agent | any                             | create it after the first deploy, then uncomment the rule |
fn report_connection(
    agent: &FleetAgent,
    rule: &ProxyRule,
    need: Need,
    reported: &mut BTreeSet<String>,
    out: &mut Export,
) {
    let Some(connection) = rule.headers.iter().find_map(|header| match &header.value {
        HeaderValue::Connection { connection, .. } => Some(connection.clone()),
        HeaderValue::WorkspaceSecret(_) => None,
    }) else {
        return;
    };
    let slug = connection.slug;
    if !reported.insert(slug.clone()) {
        return;
    }
    let hosts = host_list(&rule.match_hosts);
    let file = sandbox_file(out.blueprint.language);
    let exists = agent
        .workspace_connections
        .iter()
        .any(|existing| existing.slug == slug);
    let secret_env = format!("{}_CLIENT_SECRET", env_name(&slug));
    // Signs the deployment in while creating an agent-owned connection.
    let authorize = connection.owner == ConnectionOwner::Agent;
    let create = match &need {
        Need::Existing { connection } if connection.kind == "secret" => {
            let env = env_name(&slug);
            format!("Put the secret's value in `.env` as `{env}`, then create the connection: `mda connections create {slug} --secret-from-env {env}`")
        }
        // The connection list doesn't say which service a connection signs
        // in to, so its command takes the endpoints.
        Need::Existing { .. } => oauth_create(
            &slug,
            None,
            &[],
            &secret_env,
            authorize,
        ),
        Need::OAuth { source } => oauth_create(
            &slug,
            oauth_service(source),
            &source.scopes,
            &secret_env,
            authorize,
        ),
        Need::Secret { key } => format!(
            "Put the value of the Fleet workspace secret `{key}` in `.env` as `{key}`, then create the connection: `mda connections create {slug} --secret-from-env {key}`"
        ),
    };
    let item = match connection.owner {
        ConnectionOwner::User if exists => {
            out.report.note(format!(
                "Connections: the sandbox uses your existing `{slug}` connection to sign in to {hosts}."
            ));
            return;
        }
        ConnectionOwner::User => ConnectionToCreate {
            slug,
            after_first_deploy: false,
            summary: format!(
                "The sandbox signs in to {hosts} with each user's own account. Fleet signed in through its own OAuth app; in MDA you register your own. Each user signs in the first time the agent needs it."
            ),
            steps: oauth_steps(&need, create),
        },
        // Agent-owned connections are created per deployment, so an existing
        // slug doesn't help and creating one needs the deployment.
        ConnectionOwner::Agent => {
            let account = match &need {
                Need::Secret { key } => format!(
                    "sends the Fleet workspace secret `{key}` to {hosts} as Basic auth, through a connection the agent owns"
                ),
                _ => format!("signs in to {hosts} with an account the agent owns"),
            };
            let mut steps = vec!["Deploy: `mda deploy`".to_string()];
            steps.extend(oauth_steps(&need, create));
            steps.push(format!(
                "In `{file}`, uncomment the rules that use `{slug}`."
            ));
            steps.push("Deploy again: `mda deploy`".to_string());
            ConnectionToCreate {
                slug,
                after_first_deploy: true,
                summary: format!(
                    "The sandbox {account}. An agent-owned connection can only be created after the deployment exists, so the rules that use it are commented out in `{file}`."
                ),
                steps,
            }
        }
    };
    out.report.connection(item);
}

/// The steps that create the connection: registering an OAuth app first when
/// the connection needs one, then `create`.
fn oauth_steps(need: &Need, create: String) -> Vec<String> {
    let register = match need {
        Need::OAuth { source } => match oauth_service(source) {
            Some(service) => format!(
                "Register an OAuth app at {}.",
                service.client_registration_url
            ),
            None => format!(
                "Register an OAuth app with the service behind Fleet's `{}` provider.",
                source.provider_id
            ),
        },
        Need::Existing { connection } if connection.kind == "oauth2" => format!(
            "Register an OAuth app with the service the workspace's `{}` connection signs in to.",
            connection.slug
        ),
        _ => return vec![create],
    };
    vec![register, create]
}

/// The `mda connections create` command for an OAuth connection: the catalog
/// form when MDA knows the service, the custom-endpoint form otherwise, with
/// the scopes Fleet requested. `authorize` signs the deployment in, for an
/// agent-owned connection.
fn oauth_create(
    slug: &str,
    service: Option<&catalog::OAuthService>,
    scopes: &[String],
    secret_env: &str,
    authorize: bool,
) -> String {
    let (endpoints, catalog_hint) = match service {
        Some(service) => (format!("--oauth {}", service.service), ""),
        None => (
            "--authorize-url <authorization URL> --token-url <token URL>".to_string(),
            " If `mda connections catalog` lists the service, pass `--oauth <service>` instead of `--authorize-url` and `--token-url`.",
        ),
    };
    let scopes: String = scopes
        .iter()
        .map(|scope| format!(" --scope {scope}"))
        .collect();
    let authorize = if authorize { " --authorize" } else { "" };
    format!(
        "Create the connection. It prints the redirect URI to add to your app: `mda connections create {slug} {endpoints} --client-id <client id> --secret-from-env {secret_env}{scopes}{authorize}`.{catalog_hint}"
    )
}

/// `a`, `a and b`, or `a, b and c`.
fn host_list(hosts: &[String]) -> String {
    match hosts {
        [] => String::new(),
        [only] => only.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

/// The `mda connections catalog` entry for a Fleet provider, matched by the
/// provider id without its `-oauth-provider` suffix.
fn oauth_service(source: &RuleSource) -> Option<&'static catalog::OAuthService> {
    catalog::find(
        source
            .provider_id
            .strip_suffix("-oauth-provider")
            .unwrap_or(&source.provider_id),
    )
}

/// An environment variable name for `slug`: letters, digits and underscores.
fn env_name(slug: &str) -> String {
    slug.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect()
}

/// The file `sandbox/` is written to for the project's language.
fn sandbox_file(language: Language) -> &'static str {
    match language {
        Language::Python => "sandbox/__init__.py",
        Language::TypeScript => "sandbox/index.ts",
    }
}

/// Valid as a rule name and as a connection slug.
fn slug(text: &str) -> String {
    let slug = canonical_name(text);
    if slug.is_empty() {
        "rule".to_string()
    } else {
        slug
    }
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::*;

    fn inject(value: Value) -> Inject {
        serde_json::from_value(json!({ "header": "Authorization", "value": value })).unwrap()
    }

    #[test]
    fn connection_format_maps_each_fleet_header_value() {
        let template = |text: &str| json!({ "type": "template", "template": text });
        let basic = |username: &str, password: &str| json!({ "type": "http_basic", "username": username, "password": password });
        for (value, expected) in [
            (template("{access_token}"), Some(ConnectionFormat::Raw)),
            (template(" {value} "), Some(ConnectionFormat::Raw)),
            (
                template("Bearer {access_token}"),
                Some(ConnectionFormat::Bearer),
            ),
            (template("Bearer {value}"), Some(ConnectionFormat::Bearer)),
            (
                template("token {access_token}"),
                Some(ConnectionFormat::Template("token {token}".to_string())),
            ),
            (
                template("{value}:{access_token}"),
                Some(ConnectionFormat::Template("{token}:{token}".to_string())),
            ),
            (template("static"), None),
            (
                basic("x-access-token", "{access_token}"),
                Some(ConnectionFormat::Basic {
                    username: "x-access-token".to_string(),
                }),
            ),
            (basic("{value}", "{access_token}"), None),
            (basic("a:b", "{access_token}"), None),
            (basic(" ", "{access_token}"), None),
            (basic("user", "literal"), None),
            (json!({ "type": "other" }), None),
        ] {
            assert_eq!(
                connection_format(&inject(value.clone())),
                expected,
                "{value}"
            );
        }
    }

    #[test]
    fn network_merges_profiles_into_one_list() {
        let allow = |hosts: &[&str]| json!({ "type": "allow", "hosts": hosts });
        let deny = |hosts: &[&str]| json!({ "type": "deny", "hosts": hosts });
        let block_all = json!({ "type": "block_all" });
        let hosts = |hosts: &[&str]| hosts.iter().map(|host| host.to_string()).collect();
        for (networks, expected) in [
            (vec![], None),
            (vec![json!({ "type": "other" })], None),
            (
                vec![allow(&["a"])],
                Some(AccessControl::AllowList(hosts(&["a"]))),
            ),
            (
                vec![allow(&["a"]), allow(&["b"])],
                Some(AccessControl::AllowList(hosts(&["a", "b"]))),
            ),
            (
                vec![deny(&["a"]), deny(&["b"])],
                Some(AccessControl::DenyList(hosts(&["a", "b"]))),
            ),
            (
                vec![block_all.clone()],
                Some(AccessControl::DenyList(hosts(&["*"]))),
            ),
            (
                vec![deny(&["a"]), block_all.clone()],
                Some(AccessControl::DenyList(hosts(&["a", "*"]))),
            ),
            (
                vec![allow(&["a"]), deny(&["b"])],
                Some(AccessControl::DenyList(hosts(&["*"]))),
            ),
            (
                vec![deny(&["b"]), allow(&["a"])],
                Some(AccessControl::DenyList(hosts(&["*"]))),
            ),
            (
                vec![allow(&["a"]), block_all],
                Some(AccessControl::DenyList(hosts(&["*"]))),
            ),
        ] {
            let mut options = SandboxOptions::default();
            for kind in &networks {
                let profile: AccessProfile =
                    serde_json::from_value(json!({ "id": "p", "network": kind })).unwrap();
                network(&profile, &mut options);
            }
            assert_eq!(options.access_control, expected, "{networks:?}");
        }
    }
}
