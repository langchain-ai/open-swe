//! Endpoints are transcribed from each vendor's own OAuth documentation rather
//! than copied from a third-party catalog. Auth.js's provider list is only a
//! discovery source of which services expose public OAuth apps; tenant-specific
//! issuers, email/passkey/SAML, and Apple's JWT client-secret flow are omitted.

use std::io::{self, Write};

use anyhow::{Context, Result};
use serde::{Serialize, Serializer};

use crate::command::CommandCompletion;

/// RFC 6749 requires client support for Basic and only says servers MAY accept
/// credentials in the body.
pub const DEFAULT_AUTH_METHOD: &str = "client_secret_basic";

/// A client with no secret, which is stated rather than inferred.
pub const PUBLIC_CLIENT: &str = "none";

const AUTH_METHOD_POST: &str = "client_secret_post";

#[derive(Debug, Serialize)]
pub struct OAuthService {
    pub service: &'static str,
    pub display_name: &'static str,
    pub authorization_url: &'static str,
    pub token_url: &'static str,
    pub token_endpoint_auth_method: &'static str,
    #[serde(serialize_with = "params_as_map")]
    pub authorization_params: &'static [(&'static str, &'static str)],
    pub default_scopes: &'static [&'static str],
    pub client_registration_url: &'static str,
}

const SERVICES: &[OAuthService] = &[
    OAuthService {
        service: "atlassian",
        display_name: "Atlassian",
        authorization_url: "https://auth.atlassian.com/authorize",
        token_url: "https://auth.atlassian.com/oauth/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[("audience", "api.atlassian.com")],
        default_scopes: &["read:me", "offline_access"],
        client_registration_url: "https://developer.atlassian.com/console/myapps/",
    },
    OAuthService {
        service: "bitbucket",
        display_name: "Bitbucket",
        authorization_url: "https://bitbucket.org/site/oauth2/authorize",
        token_url: "https://bitbucket.org/site/oauth2/access_token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[],
        default_scopes: &["account"],
        client_registration_url: "https://bitbucket.org/account/settings/app-auth/",
    },
    OAuthService {
        service: "box",
        display_name: "Box",
        authorization_url: "https://account.box.com/api/oauth2/authorize",
        token_url: "https://api.box.com/oauth2/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &[],
        client_registration_url: "https://app.box.com/developers/console",
    },
    OAuthService {
        service: "click-up",
        display_name: "ClickUp",
        authorization_url: "https://app.clickup.com/api",
        token_url: "https://api.clickup.com/api/v2/oauth/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &[],
        client_registration_url: "https://app.clickup.com/settings/apps",
    },
    OAuthService {
        service: "discord",
        display_name: "Discord",
        authorization_url: "https://discord.com/api/oauth2/authorize",
        token_url: "https://discord.com/api/oauth2/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["identify", "email"],
        client_registration_url: "https://discord.com/developers/applications",
    },
    OAuthService {
        service: "dropbox",
        display_name: "Dropbox",
        authorization_url: "https://www.dropbox.com/oauth2/authorize",
        token_url: "https://api.dropboxapi.com/oauth2/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[("token_access_type", "offline")],
        default_scopes: &["account_info.read"],
        client_registration_url: "https://www.dropbox.com/developers/apps",
    },
    OAuthService {
        service: "facebook",
        display_name: "Facebook",
        authorization_url: "https://www.facebook.com/dialog/oauth",
        token_url: "https://graph.facebook.com/oauth/access_token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["email", "public_profile"],
        client_registration_url: "https://developers.facebook.com/apps/",
    },
    OAuthService {
        service: "figma",
        display_name: "Figma",
        authorization_url: "https://www.figma.com/oauth",
        token_url: "https://api.figma.com/v1/oauth/token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[],
        default_scopes: &["current_user:read"],
        client_registration_url: "https://www.figma.com/developers/apps",
    },
    OAuthService {
        service: "github",
        display_name: "GitHub",
        authorization_url: "https://github.com/login/oauth/authorize",
        token_url: "https://github.com/login/oauth/access_token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["read:user"],
        client_registration_url: "https://github.com/settings/developers",
    },
    OAuthService {
        service: "gitlab",
        display_name: "GitLab",
        authorization_url: "https://gitlab.com/oauth/authorize",
        token_url: "https://gitlab.com/oauth/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["read_user"],
        client_registration_url: "https://gitlab.com/-/user_settings/applications",
    },
    OAuthService {
        service: "google",
        display_name: "Google",
        authorization_url: "https://accounts.google.com/o/oauth2/v2/auth",
        token_url: "https://oauth2.googleapis.com/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[("access_type", "offline"), ("prompt", "consent")],
        default_scopes: &["openid", "https://www.googleapis.com/auth/userinfo.email"],
        client_registration_url: "https://console.cloud.google.com/apis/credentials",
    },
    OAuthService {
        service: "hubspot",
        display_name: "HubSpot",
        authorization_url: "https://app.hubspot.com/oauth/authorize",
        token_url: "https://api.hubapi.com/oauth/v1/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["oauth"],
        client_registration_url: "https://developers.hubspot.com/",
    },
    OAuthService {
        service: "huggingface",
        display_name: "Hugging Face",
        authorization_url: "https://huggingface.co/oauth/authorize",
        token_url: "https://huggingface.co/oauth/token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[],
        default_scopes: &["openid", "profile", "email"],
        client_registration_url: "https://huggingface.co/settings/applications/new",
    },
    OAuthService {
        service: "linear",
        display_name: "Linear",
        authorization_url: "https://linear.app/oauth/authorize",
        token_url: "https://api.linear.app/oauth/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["read"],
        client_registration_url: "https://linear.app/settings/api/applications/new",
    },
    OAuthService {
        service: "linkedin",
        display_name: "LinkedIn",
        authorization_url: "https://www.linkedin.com/oauth/v2/authorization",
        token_url: "https://www.linkedin.com/oauth/v2/accessToken",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["openid", "profile", "email"],
        client_registration_url: "https://www.linkedin.com/developers/apps",
    },
    OAuthService {
        service: "notion-api",
        display_name: "Notion API",
        authorization_url: "https://api.notion.com/v1/oauth/authorize",
        token_url: "https://api.notion.com/v1/oauth/token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[("owner", "user")],
        default_scopes: &[],
        client_registration_url: "https://www.notion.so/my-integrations",
    },
    OAuthService {
        service: "patreon",
        display_name: "Patreon",
        authorization_url: "https://www.patreon.com/oauth2/authorize",
        token_url: "https://www.patreon.com/api/oauth2/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["identity", "identity[email]"],
        client_registration_url: "https://www.patreon.com/portal/registration/register-clients",
    },
    OAuthService {
        service: "reddit",
        display_name: "Reddit",
        authorization_url: "https://www.reddit.com/api/v1/authorize",
        token_url: "https://www.reddit.com/api/v1/access_token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[("duration", "permanent")],
        default_scopes: &["identity"],
        client_registration_url: "https://www.reddit.com/prefs/apps",
    },
    OAuthService {
        service: "salesforce",
        display_name: "Salesforce",
        authorization_url: "https://login.salesforce.com/services/oauth2/authorize",
        token_url: "https://login.salesforce.com/services/oauth2/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["api", "refresh_token"],
        client_registration_url: "https://login.salesforce.com/",
    },
    OAuthService {
        service: "slack",
        display_name: "Slack",
        authorization_url: "https://slack.com/oauth/v2/authorize",
        token_url: "https://slack.com/api/oauth.v2.access",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["chat:write"],
        client_registration_url: "https://api.slack.com/apps",
    },
    OAuthService {
        service: "spotify",
        display_name: "Spotify",
        authorization_url: "https://accounts.spotify.com/authorize",
        token_url: "https://accounts.spotify.com/api/token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[],
        default_scopes: &["user-read-email"],
        client_registration_url: "https://developer.spotify.com/dashboard",
    },
    OAuthService {
        service: "stripe-link",
        display_name: "Stripe Link",
        authorization_url: "https://login.link.com/auth",
        token_url: "https://login.link.com/auth/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["payment_methods.agentic", "userinfo:read"],
        client_registration_url: "https://docs.stripe.com/agentic-commerce/link-cli/oauth",
    },
    OAuthService {
        service: "twitch",
        display_name: "Twitch",
        authorization_url: "https://id.twitch.tv/oauth2/authorize",
        token_url: "https://id.twitch.tv/oauth2/token",
        token_endpoint_auth_method: AUTH_METHOD_POST,
        authorization_params: &[],
        default_scopes: &["user:read:email"],
        client_registration_url: "https://dev.twitch.tv/console/apps",
    },
    OAuthService {
        service: "x",
        display_name: "X",
        authorization_url: "https://x.com/i/oauth2/authorize",
        token_url: "https://api.x.com/2/oauth2/token",
        token_endpoint_auth_method: DEFAULT_AUTH_METHOD,
        authorization_params: &[],
        default_scopes: &["tweet.read", "users.read", "offline.access"],
        client_registration_url: "https://developer.x.com/en/portal/dashboard",
    },
];

pub struct ShowOptions {
    pub json: bool,
}

pub fn find(service: &str) -> Option<&'static OAuthService> {
    let service = service.trim();
    SERVICES
        .iter()
        .find(|entry| entry.service.eq_ignore_ascii_case(service))
}

pub fn service_names() -> String {
    SERVICES
        .iter()
        .map(|entry| entry.service)
        .collect::<Vec<_>>()
        .join(", ")
}

pub fn show(opts: ShowOptions) -> Result<CommandCompletion> {
    let mut stdout = io::stdout().lock();
    write_services(&mut stdout, SERVICES, opts.json)?;
    Ok(CommandCompletion::Succeeded)
}

fn params_as_map<S>(
    params: &&'static [(&'static str, &'static str)],
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    serializer.collect_map(params.iter().copied())
}

fn write_services(out: &mut impl Write, services: &[OAuthService], json: bool) -> Result<()> {
    let written = if json {
        serde_json::to_writer_pretty(&mut *out, services)
            .map_err(io::Error::from)
            .and_then(|()| writeln!(out))
            .and_then(|()| out.flush())
    } else {
        write_table(&mut *out, services)
    };
    match written {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::BrokenPipe => Ok(()),
        Err(err) => Err(err).context("failed to write the OAuth catalog to stdout"),
    }
}

fn write_table(out: &mut impl Write, services: &[OAuthService]) -> io::Result<()> {
    let scopes: Vec<String> = services.iter().map(scope_summary).collect();
    let service_width = super::column_width("SERVICE", services.iter().map(|item| item.service));
    let name_width = super::column_width("NAME", services.iter().map(|item| item.display_name));
    let scope_width = super::column_width("DEFAULT SCOPES", scopes.iter().map(String::as_str));
    writeln!(
        out,
        "{:<service_width$}  {:<name_width$}  {:<scope_width$}  REGISTER AT",
        "SERVICE", "NAME", "DEFAULT SCOPES"
    )?;
    for (item, scopes) in services.iter().zip(&scopes) {
        writeln!(
            out,
            "{:<service_width$}  {:<name_width$}  {:<scope_width$}  {}",
            item.service, item.display_name, scopes, item.client_registration_url
        )?;
    }
    out.flush()
}

fn scope_summary(service: &OAuthService) -> String {
    if service.default_scopes.is_empty() {
        return "—".to_string();
    }
    service.default_scopes.join(" ")
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::*;

    #[test]
    fn every_entry_is_well_formed() {
        let mut seen = BTreeSet::new();
        for entry in SERVICES {
            assert!(seen.insert(entry.service), "duplicate: {}", entry.service);
            assert_eq!(
                entry.service,
                entry.service.to_ascii_lowercase(),
                "{} is not lowercase",
                entry.service
            );
            assert!(!entry.display_name.is_empty(), "{}", entry.service);
            for url in [
                entry.authorization_url,
                entry.token_url,
                entry.client_registration_url,
            ] {
                assert!(
                    url.starts_with("https://"),
                    "{} has a non-https url: {url}",
                    entry.service
                );
            }
            assert!(
                matches!(
                    entry.token_endpoint_auth_method,
                    DEFAULT_AUTH_METHOD | AUTH_METHOD_POST | PUBLIC_CLIENT
                ),
                "{} has an unsupported auth method: {}",
                entry.service,
                entry.token_endpoint_auth_method
            );
            for (key, value) in entry.authorization_params {
                assert!(!key.is_empty(), "{}", entry.service);
                assert!(!value.is_empty(), "{}", entry.service);
            }
        }
        let names: Vec<_> = SERVICES.iter().map(|entry| entry.service).collect();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted, "catalog must stay sorted by service name");
    }

    #[test]
    fn find_resolves_a_newly_cataloged_service() {
        let gitlab = find("GitLab").expect("gitlab is in the catalog");
        assert_eq!(gitlab.service, "gitlab");
        assert_eq!(
            gitlab.authorization_url,
            "https://gitlab.com/oauth/authorize"
        );
        let atlassian = find("atlassian").expect("atlassian is in the catalog");
        assert_eq!(
            atlassian.authorization_params,
            &[("audience", "api.atlassian.com")]
        );
    }

    #[test]
    fn find_ignores_case_and_surrounding_space() {
        assert_eq!(find("notion-api").unwrap().display_name, "Notion API");
        assert_eq!(find("Notion-API").unwrap().display_name, "Notion API");
        assert_eq!(find("  notion-api  ").unwrap().display_name, "Notion API");
        assert!(find("notion").is_none());
        assert!(find("nope").is_none());
        assert!(find("").is_none());
    }

    #[test]
    fn bitbucket_and_figma_use_http_basic() {
        assert_eq!(
            find("bitbucket").unwrap().token_endpoint_auth_method,
            DEFAULT_AUTH_METHOD
        );
        assert_eq!(
            find("figma").unwrap().token_endpoint_auth_method,
            DEFAULT_AUTH_METHOD
        );
    }

    #[test]
    fn find_resolves_x_from_vendor_docs() {
        let x = find("x").expect("x is in the catalog");
        assert_eq!(x.display_name, "X");
        assert_eq!(x.authorization_url, "https://x.com/i/oauth2/authorize");
        assert_eq!(x.token_url, "https://api.x.com/2/oauth2/token");
        assert_eq!(x.token_endpoint_auth_method, DEFAULT_AUTH_METHOD);
        assert_eq!(
            x.default_scopes,
            &["tweet.read", "users.read", "offline.access"]
        );
        assert_eq!(
            x.client_registration_url,
            "https://developer.x.com/en/portal/dashboard"
        );
        assert!(x.authorization_params.is_empty());
    }

    #[test]
    fn find_resolves_stripe_link_from_vendor_docs() {
        let stripe_link = find("stripe-link").expect("stripe-link is in the catalog");

        assert_eq!(stripe_link.display_name, "Stripe Link");
        assert_eq!(stripe_link.authorization_url, "https://login.link.com/auth");
        assert_eq!(stripe_link.token_url, "https://login.link.com/auth/token");
        assert_eq!(stripe_link.token_endpoint_auth_method, AUTH_METHOD_POST);
        assert_eq!(
            stripe_link.default_scopes,
            &["payment_methods.agentic", "userinfo:read"]
        );
        assert!(stripe_link.authorization_params.is_empty());
        assert_eq!(
            stripe_link.client_registration_url,
            "https://docs.stripe.com/agentic-commerce/link-cli/oauth"
        );
    }

    #[test]
    fn the_table_reports_scopes_and_where_to_register() {
        let mut out = Vec::new();
        write_services(&mut out, SERVICES, false).unwrap();
        let rendered = String::from_utf8(out).unwrap();
        assert!(rendered.contains("SERVICE"), "{rendered}");
        assert!(rendered.contains("DEFAULT SCOPES"), "{rendered}");
        assert!(rendered.contains("REGISTER AT"), "{rendered}");
        assert!(rendered.contains("notion-api"), "{rendered}");
        assert!(
            rendered.contains("https://www.notion.so/my-integrations"),
            "{rendered}"
        );
    }

    #[test]
    fn json_output_serializes_params_as_an_object() {
        let mut out = Vec::new();
        write_services(&mut out, SERVICES, true).unwrap();
        let parsed: serde_json::Value = serde_json::from_slice(&out).unwrap();
        let google = parsed
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["service"] == "google")
            .unwrap();
        assert_eq!(google["authorization_params"]["access_type"], "offline");
        assert_eq!(google["token_endpoint_auth_method"], "client_secret_post");
    }
}
