//! Compare what the channel needs with what the GitHub App has, and word the
//! result for the deploy output. Pure, so every message is unit-tested.

use std::collections::{BTreeMap, BTreeSet};

use super::app_manifest::{app_permissions_url, install_url};
use super::manifest::{GitHubChannel, PermissionLevel};
use crate::langsmith::github_channel::{GitHubChannelStatus, GitHubInstallation};

/// Repositories listed by name before the summary switches to a count.
const LISTED_REPOSITORIES: usize = 5;
/// Accounts named before a list of installations or requests switches to a
/// count: a public App can have hundreds. Past the 100 Agent Auth reports,
/// the list is cut off and its counts are lower bounds.
const LISTED_ACCOUNTS: usize = 3;

#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct Report {
    /// Shown as success when the App is installed somewhere.
    pub(crate) ready: Option<String>,
    /// Things someone must fix on GitHub. Each names who acts and where.
    pub(crate) warnings: Vec<String>,
    /// Harmless observations.
    pub(crate) notes: Vec<String>,
}

/// What GitHub reports about the App, worded for the deploy output. `own` is
/// the installation the agent's token is minted on, when known: what is wrong
/// with it is a warning of its own, while other accounts' installations and
/// requests are counted, with a few named, and only noted, since they do not
/// affect this agent. Without it, every installation might be the agent's,
/// so they stay warnings, still counted when there are many.
pub(crate) fn build(
    channel: &GitHubChannel,
    status: &GitHubChannelStatus,
    own: Option<u64>,
) -> Report {
    let app = &status.app;
    let owner_is_org = app.owner.is_organization();
    let permissions_url = app_permissions_url(&app.owner.login, owner_is_org, &app.slug);
    let mut report = Report::default();

    for name in channel.permissions.keys() {
        if KNOWN_PERMISSIONS.contains(&name.as_str()) {
            continue;
        }
        let hint = match similar_permissions(name).as_slice() {
            [] => String::new(),
            similar => format!(" Did you mean {}?", or_list(similar)),
        };
        report.warnings.push(format!(
            "app.permissions names {name}, which GitHub does not list as an App permission.{hint}"
        ));
    }

    if let Some(changes) = app_changes(channel, status, &permissions_url) {
        report.warnings.push(changes);
    }

    let own = status
        .installations
        .iter()
        .find(|installation| Some(installation.id) == own);
    let others: Vec<&GitHubInstallation> = status
        .installations
        .iter()
        .filter(|installation| own.is_none_or(|own| own.id != installation.id))
        .collect();
    // Others' trouble is only noted once the agent has an installation that
    // works; until then it may be the agent's.
    let others_noted = own.is_some_and(|own| own.suspended_at.is_none());
    let cut = status.installations_truncated;
    let mut about_others = Vec::new();

    let requests = &status.installation_requests;
    if requests.len() <= LISTED_ACCOUNTS {
        about_others.extend(requests.iter().map(|request| {
            let requester = request
                .requester
                .as_ref()
                .map(|requester| format!(" requested by {}", requester.login))
                .unwrap_or_default();
            format!(
                "Waiting for an owner of {} to approve the installation{requester}.",
                request.account.login
            )
        }));
    } else {
        let accounts = distinct(
            requests
                .iter()
                .map(|request| request.account.login.as_str()),
        );
        let requests_cut = status.installation_requests_truncated;
        about_others.push(format!(
            "Waiting for owners of {} accounts to approve installing the App: {}.",
            at_least(accounts.len(), requests_cut),
            sample(&accounts, requests_cut)
        ));
    }

    if status.installations.is_empty() {
        report.warnings.append(&mut about_others);
        report.warnings.push(format!(
            "GitHub App {} is not installed yet. Install it on your repositories at {}",
            app.slug,
            install_url(&app.slug)
        ));
        return report;
    }

    if let Some(own) = own {
        report
            .warnings
            .extend(installation_problems(channel, status, own));
    }
    let other = if own.is_some() { "other " } else { "" };

    let suspended: Vec<&GitHubInstallation> = others
        .iter()
        .copied()
        .filter(|installation| installation.suspended_at.is_some())
        .collect();
    if suspended.len() <= LISTED_ACCOUNTS {
        about_others.extend(
            suspended
                .iter()
                .map(|installation| suspended_message(installation)),
        );
    } else {
        about_others.push(format!(
            "The installations on {} {other}accounts are suspended, so they receive no events: {}. Their owners unsuspend them in each installation's settings on GitHub.",
            at_least(suspended.len(), cut),
            sample(&logins(&suspended), cut)
        ));
    }

    let active: Vec<&GitHubInstallation> = others
        .iter()
        .copied()
        .filter(|installation| installation.suspended_at.is_none())
        .collect();
    // The agent's token reaches only its own installation's repositories,
    // so others are checked only when that one is unknown.
    if own.is_none() {
        let lacking: Vec<&GitHubInstallation> = active
            .iter()
            .copied()
            .filter(|installation| missing_repositories(channel, installation).is_some())
            .collect();
        if lacking.len() <= LISTED_ACCOUNTS {
            about_others.extend(
                lacking
                    .iter()
                    .filter_map(|installation| repositories_message(channel, installation)),
            );
        } else {
            about_others.push(format!(
                "The installations on {} accounts do not include every repository this channel is limited to ({}): {}. Add them in each installation's settings on GitHub.",
                at_least(lacking.len(), cut),
                channel.repositories.join(", "),
                sample(&logins(&lacking), cut)
            ));
        }
    }

    let behind: Vec<&GitHubInstallation> = active
        .iter()
        .copied()
        .filter(|installation| !unaccepted(status, installation).is_empty())
        .collect();
    if behind.len() <= LISTED_ACCOUNTS {
        about_others.extend(
            behind
                .iter()
                .filter_map(|installation| permissions_message(status, installation)),
        );
    } else {
        let pending: BTreeSet<String> = behind
            .iter()
            .flat_map(|installation| unaccepted(status, installation))
            .collect();
        about_others.push(format!(
            "Owners of {} {other}accounts have not accepted the App's updated permissions ({}) yet: {}. Each owner accepts them in the installation's settings on GitHub.",
            at_least(behind.len(), cut),
            pending.into_iter().collect::<Vec<_>>().join(", "),
            sample(&logins(&behind), cut)
        ));
    }

    if others_noted {
        report.notes.append(&mut about_others);
    } else {
        report.warnings.append(&mut about_others);
    }

    let prefix = format!(
        "GitHub channel ready · App {} ({})",
        app.slug, app.owner.login
    );
    report.ready = match (own.filter(|own| own.suspended_at.is_none()), active.len()) {
        (None, 0) => None,
        (Some(own), 0) => Some(format!(
            "{prefix} · installed on {}",
            installation_scope(own)
        )),
        (Some(own), _) => Some(format!(
            "{prefix} · this agent uses {} · also installed on {}",
            installation_scope(own),
            installations_scope(&active, "other ", cut)
        )),
        (None, _) => Some(format!(
            "{prefix} · installed on {}",
            installations_scope(&active, "", cut)
        )),
    };
    report
}

/// What is wrong with one installation, in full: the agent's own.
fn installation_problems(
    channel: &GitHubChannel,
    status: &GitHubChannelStatus,
    installation: &GitHubInstallation,
) -> Vec<String> {
    if installation.suspended_at.is_some() {
        return vec![suspended_message(installation)];
    }
    [
        repositories_message(channel, installation),
        permissions_message(status, installation),
    ]
    .into_iter()
    .flatten()
    .collect()
}

fn suspended_message(installation: &GitHubInstallation) -> String {
    format!(
        "The installation on {} is suspended, so it receives no events. Unsuspend it at {}",
        installation.account.login,
        installation_settings_url(installation)
    )
}

fn repositories_message(
    channel: &GitHubChannel,
    installation: &GitHubInstallation,
) -> Option<String> {
    let repositories = missing_repositories(channel, installation)?;
    Some(format!(
        "The installation on {} does not include {}, which this channel is limited to. Add them at {}",
        installation.account.login,
        repositories.join(", "),
        installation_settings_url(installation)
    ))
}

fn permissions_message(
    status: &GitHubChannelStatus,
    installation: &GitHubInstallation,
) -> Option<String> {
    let pending = unaccepted(status, installation);
    (!pending.is_empty()).then(|| {
        format!(
            "An owner of {} must accept the App's updated permissions ({}) at {}",
            installation.account.login,
            pending.join(", "),
            installation_settings_url(installation)
        )
    })
}

/// The App's permissions an installation has not accepted yet.
fn unaccepted(status: &GitHubChannelStatus, installation: &GitHubInstallation) -> Vec<String> {
    missing(
        &parse_levels(&status.app.permissions),
        &installation.permissions,
    )
}

/// Where active installations reach: each in full when there are few, else
/// a count with the first accounts named. `other` qualifies the count, and
/// `truncated` makes it a lower bound.
fn installations_scope(
    installations: &[&GitHubInstallation],
    other: &str,
    truncated: bool,
) -> String {
    if installations.len() <= LISTED_ACCOUNTS {
        return installations
            .iter()
            .map(|installation| installation_scope(installation))
            .collect::<Vec<_>>()
            .join("; ");
    }
    format!(
        "{} {other}accounts ({})",
        at_least(installations.len(), truncated),
        sample(&logins(installations), truncated)
    )
}

fn logins<'a>(installations: &[&'a GitHubInstallation]) -> Vec<&'a str> {
    installations
        .iter()
        .map(|installation| installation.account.login.as_str())
        .collect()
}

/// Names in order, each once.
fn distinct<'a>(names: impl Iterator<Item = &'a str>) -> Vec<&'a str> {
    let mut seen = BTreeSet::new();
    names.filter(|name| seen.insert(*name)).collect()
}

/// `a, b and c`, or `a, b, c and 42 more` past [`LISTED_ACCOUNTS`]; from a
/// cut-off list, `a, b, c and at least 42 more`.
fn sample(names: &[&str], truncated: bool) -> String {
    if names.len() <= LISTED_ACCOUNTS && !truncated {
        return and_list(names);
    }
    let listed = names[..names.len().min(LISTED_ACCOUNTS)].join(", ");
    match (names.len().saturating_sub(LISTED_ACCOUNTS), truncated) {
        (more, false) => format!("{listed} and {more} more"),
        (0, true) => format!("{listed} and more"),
        (more, true) => format!("{listed} and at least {more} more"),
    }
}

/// `count`, or `at least count` when it comes from a cut-off list.
fn at_least(count: usize, truncated: bool) -> String {
    if truncated {
        format!("at least {count}")
    } else {
        count.to_string()
    }
}

/// One list of what the App owner must add on GitHub and what they can
/// remove, or `None` when the App matches the channel.
fn app_changes(
    channel: &GitHubChannel,
    status: &GitHubChannelStatus,
    permissions_url: &str,
) -> Option<String> {
    let app = &status.app;
    let mut add: Vec<Row> = channel
        .permissions
        .iter()
        .filter_map(|(name, needed)| {
            let level = match app.permissions.get(name) {
                None => needed.as_str().to_string(),
                // An unknown level (`admin`) covers what the channel needs.
                Some(granted) if PermissionLevel::parse(granted).is_none_or(|g| g >= *needed) => {
                    return None
                }
                Some(granted) => format!("{granted} → {}", needed.as_str()),
            };
            Some(Row {
                name: name.clone(),
                level,
                reason: why(channel, name),
            })
        })
        .collect();
    add.extend(
        channel
            .events
            .iter()
            .filter(|event| !app.events.contains(event))
            .map(|event| Row {
                name: event.clone(),
                level: "event".to_string(),
                reason: format!(
                    "to receive {}",
                    channel
                        .handled
                        .iter()
                        .filter(|key| key.split_once('.').map_or(key.as_str(), |(e, _)| e) == event)
                        .cloned()
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            }),
    );
    let remove: Vec<Row> = app
        .permissions
        .iter()
        .filter_map(|(name, granted)| {
            let (level, reason) = match channel.permissions.get(name) {
                None => (granted.clone(), "unused".to_string()),
                Some(needed) if PermissionLevel::parse(granted).is_none_or(|g| g > *needed) => (
                    format!("{granted} → {}", needed.as_str()),
                    format!("the channel needs only {}", needed.as_str()),
                ),
                Some(_) => return None,
            };
            Some(Row {
                name: name.clone(),
                level,
                reason,
            })
        })
        .collect();
    if add.is_empty() && remove.is_empty() {
        return None;
    }

    let (name_width, level_width) = widths(add.iter().chain(&remove));
    let mut lines = vec![
        format!(
            "GitHub App {}'s permissions do not match this channel.",
            app.slug
        ),
        String::new(),
        "GitHub has no API to change an App's permissions, so the App owner makes the changes below at"
            .to_string(),
        permissions_url.to_string(),
    ];
    if !add.is_empty() {
        lines.push(String::new());
        lines.push("Add (required):".to_string());
        lines.extend(table_lines(&add, name_width, level_width));
    }
    if !remove.is_empty() {
        lines.push(String::new());
        lines.push("Remove or lower (optional: deploy already keeps these out of the agent's token, but the App still holds them):".to_string());
        lines.extend(table_lines(&remove, name_width, level_width));
    }
    Some(lines.join("\n"))
}

/// The agent's token as deploy leaves it: what it has, from its
/// `permission:level` scopes, each with why the channel needs it, then what
/// the channel needs that it does not have yet and who grants it where.
/// `metadata`, which every App has, is left out.
pub(super) fn token_summary(
    channel: &GitHubChannel,
    status: &GitHubChannelStatus,
    installation: &GitHubInstallation,
    has: &[String],
    renewed: bool,
) -> String {
    let held: BTreeMap<&str, PermissionLevel> = has
        .iter()
        .filter_map(|scope| {
            let (name, level) = scope.split_once(':')?;
            Some((name, PermissionLevel::parse(level)?))
        })
        .collect();
    let row = |name: &str, level: PermissionLevel| Row {
        name: name.to_string(),
        level: level.as_str().to_string(),
        reason: why(channel, name),
    };
    let current: Vec<Row> = held
        .iter()
        .filter(|(name, _)| **name != "metadata")
        .map(|(name, level)| row(name, *level))
        .collect();
    let waiting: Vec<Row> = channel
        .permissions
        .iter()
        .filter(|(name, needed)| {
            name.as_str() != "metadata"
                && held.get(name.as_str()).is_none_or(|level| level < *needed)
        })
        .map(|(name, needed)| row(name, *needed))
        .collect();

    let (name_width, level_width) = widths(current.iter().chain(&waiting));
    let has = if renewed { "now has" } else { "has" };
    let mut lines = if current.is_empty() {
        vec![format!(
            "The agent's GitHub token {has} no permissions beyond reading repository metadata."
        )]
    } else {
        let mut lines = vec![format!("The agent's GitHub token {has}:")];
        lines.extend(table_lines(&current, name_width, level_width));
        lines
    };
    if waiting.is_empty() {
        return lines.join("\n");
    }
    lines.push(String::new());
    lines.push("Waiting for GitHub:".to_string());
    lines.extend(table_lines(&waiting, name_width, level_width));

    // The App must have a permission before an installation can grant it.
    let app = &status.app;
    let app_lacks: Vec<String> = waiting
        .iter()
        .filter(|row| {
            let needed = channel.permissions.get(&row.name);
            // An unknown level (`admin`) covers what the channel needs.
            !app.permissions.get(&row.name).is_some_and(|granted| {
                PermissionLevel::parse(granted)
                    .is_none_or(|granted| needed.is_none_or(|needed| granted >= *needed))
            })
        })
        .map(|row| format!("{} ({})", row.name, row.level))
        .collect();
    let mut steps = Vec::new();
    if !app_lacks.is_empty() {
        let unsubscribed: Vec<String> = channel
            .events
            .iter()
            .filter(|event| !app.events.contains(event))
            .cloned()
            .collect();
        let subscribe = match unsubscribed.as_slice() {
            [] => String::new(),
            [event] => format!(" and subscribes to the {event} event"),
            events => format!(" and subscribes to the {} events", and_list(events)),
        };
        steps.push(format!(
            "The App owner grants {}{subscribe} at {}",
            and_list(&app_lacks),
            app_permissions_url(&app.owner.login, app.owner.is_organization(), &app.slug)
        ));
    }
    // An installation past Agent Auth's report comes without its account's
    // type, so its settings URL is unknown: name the installation instead.
    if installation.account.kind.is_empty() && installation.settings_url.is_none() {
        steps.push(format!(
            "An owner of {} accepts them in that installation's settings on GitHub",
            installation.account.login
        ));
    } else {
        steps.push(format!(
            "An owner of {} accepts them at {}",
            installation.account.login,
            installation_settings_url(installation)
        ));
    }
    steps.push("Run `mda deploy` again.".to_string());
    lines.push(String::new());
    lines.extend(
        steps
            .iter()
            .enumerate()
            .map(|(index, step)| format!("{}. {step}", index + 1)),
    );
    lines.join("\n")
}

/// GitHub App permission names, from the `app-permissions` schema of GitHub's
/// REST API. GitHub adds permissions over time, so a name missing here is
/// only a warning.
const KNOWN_PERMISSIONS: &[&str] = &[
    "actions",
    "administration",
    "attestations",
    "checks",
    "codespaces",
    "contents",
    "dependabot_secrets",
    "deployments",
    "email_addresses",
    "environments",
    "followers",
    "git_ssh_keys",
    "gpg_keys",
    "interaction_limits",
    "issues",
    "members",
    "merge_queues",
    "metadata",
    "organization_administration",
    "organization_announcement_banners",
    "organization_copilot_seat_management",
    "organization_custom_org_roles",
    "organization_custom_properties",
    "organization_custom_roles",
    "organization_events",
    "organization_hooks",
    "organization_packages",
    "organization_personal_access_token_requests",
    "organization_personal_access_tokens",
    "organization_plan",
    "organization_projects",
    "organization_secrets",
    "organization_self_hosted_runners",
    "organization_user_blocking",
    "packages",
    "pages",
    "profile",
    "pull_requests",
    "repository_custom_properties",
    "repository_hooks",
    "repository_projects",
    "secret_scanning_alerts",
    "secrets",
    "security_events",
    "single_file",
    "starring",
    "statuses",
    "team_discussions",
    "vulnerability_alerts",
    "workflows",
];

/// Known permissions a mistyped or shortened name likely means: those that
/// end in `_{name}` (`projects` is `repository_projects`), or are at most
/// two edits away (`pull_request`).
fn similar_permissions(name: &str) -> Vec<&'static str> {
    let suffix = format!("_{name}");
    KNOWN_PERMISSIONS
        .iter()
        .copied()
        .filter(|known| known.ends_with(&suffix) || edit_distance(known, name) <= 2)
        .collect()
}

/// Levenshtein distance between two ASCII names.
fn edit_distance(a: &str, b: &str) -> usize {
    let b = b.as_bytes();
    let mut previous: Vec<usize> = (0..=b.len()).collect();
    for (i, ca) in a.bytes().enumerate() {
        let mut current = vec![i + 1];
        for (j, cb) in b.iter().enumerate() {
            let substitute = previous[j] + usize::from(ca != *cb);
            current.push(substitute.min(previous[j + 1] + 1).min(current[j] + 1));
        }
        previous = current;
    }
    previous[b.len()]
}

/// `a`, `a and b`, `a, b and c`.
fn and_list<S: AsRef<str>>(items: &[S]) -> String {
    joined(items, "and")
}

/// `a`, `a or b`, `a, b or c`.
fn or_list<S: AsRef<str>>(items: &[S]) -> String {
    joined(items, "or")
}

fn joined<S: AsRef<str>>(items: &[S], last: &str) -> String {
    match items {
        [] => String::new(),
        [only] => only.as_ref().to_string(),
        [rest @ .., final_item] => format!(
            "{} {last} {}",
            rest.iter()
                .map(AsRef::as_ref)
                .collect::<Vec<_>>()
                .join(", "),
            final_item.as_ref()
        ),
    }
}

/// Why the channel needs a permission: the handled events that need its
/// level, or `app.permissions`.
fn why(channel: &GitHubChannel, name: &str) -> String {
    match channel
        .event_permissions
        .iter()
        .find(|permission| permission.name == name)
    {
        Some(permission) if permission.level == PermissionLevel::Write => {
            format!("to reply to {}", permission.events.join(", "))
        }
        Some(permission) => format!("to receive {}", permission.events.join(", ")),
        None => "from app.permissions".to_string(),
    }
}

/// A permission line in the deploy output: name, level, and why.
struct Row {
    name: String,
    level: String,
    reason: String,
}

/// Column widths that align every row, in characters.
fn widths<'a>(rows: impl Iterator<Item = &'a Row>) -> (usize, usize) {
    rows.fold((0, 0), |(name, level), row| {
        (
            name.max(row.name.chars().count()),
            level.max(row.level.chars().count()),
        )
    })
}

/// Rows as indented, aligned columns.
fn table_lines(rows: &[Row], name_width: usize, level_width: usize) -> Vec<String> {
    rows.iter()
        .map(|row| {
            format!(
                "  {:name_width$}  {:level_width$}  {}",
                row.name, row.level, row.reason
            )
            .trim_end()
            .to_string()
        })
        .collect()
}

/// The channel's repositories an installation does not cover, or `None` when
/// it covers all of them. An installation on another account covers none.
/// When the listing was truncated, an unlisted repository counts as covered;
/// GitHub refuses a token for it if it is not.
pub(super) fn missing_repositories(
    channel: &GitHubChannel,
    installation: &GitHubInstallation,
) -> Option<Vec<String>> {
    let missing: Vec<String> = channel
        .repositories
        .iter()
        .filter(|repository| {
            let on_account = repository
                .split_once('/')
                .is_some_and(|(owner, _)| owner.eq_ignore_ascii_case(&installation.account.login));
            !on_account
                || (installation.repository_selection != "all"
                    && !installation.repositories_truncated
                    && !installation
                        .repositories
                        .iter()
                        .any(|listed| listed.eq_ignore_ascii_case(repository)))
        })
        .cloned()
        .collect();
    (!missing.is_empty()).then_some(missing)
}

/// Whether `granted` covers every required permission at its level.
pub(super) fn grants(
    required: &BTreeMap<String, PermissionLevel>,
    granted: &BTreeMap<String, String>,
) -> bool {
    missing(required, granted).is_empty()
}

/// The required permissions the granted set covers, as `permission:level`
/// scopes.
pub(super) fn granted_scopes(
    required: &BTreeMap<String, PermissionLevel>,
    granted: &BTreeMap<String, String>,
) -> Vec<String> {
    required
        .iter()
        .filter(|(name, level)| {
            granted
                .get(*name)
                .and_then(|value| PermissionLevel::parse(value))
                .is_some_and(|granted| granted >= **level)
        })
        .map(|(name, level)| format!("{name}:{}", level.as_str()))
        .collect()
}

/// Whether going from the `permission:level` scopes `from` to `to` drops a
/// permission or lowers its level. An empty `from` means no limit: the token
/// then has every permission of the App's installation. An empty `to` still
/// counts as narrowing, so a change never widens a token to no limit.
pub(super) fn narrows(from: &[String], to: &[String]) -> bool {
    if from.is_empty() {
        return !to.is_empty();
    }
    let level = |scope: &str| {
        let (name, level) = scope.split_once(':').unwrap_or((scope, ""));
        (name.to_string(), PermissionLevel::parse(level))
    };
    let to: BTreeMap<String, Option<PermissionLevel>> =
        to.iter().map(|scope| level(scope)).collect();
    from.iter()
        .map(|scope| level(scope))
        .any(|(name, from)| match to.get(&name) {
            None => true,
            Some(to) => to < &from,
        })
}

/// Required permissions the granted set lacks or grants at a lower level.
fn missing(
    required: &BTreeMap<String, PermissionLevel>,
    granted: &BTreeMap<String, String>,
) -> Vec<String> {
    required
        .iter()
        .filter(|(name, level)| {
            granted
                .get(*name)
                .and_then(|value| PermissionLevel::parse(value))
                .is_none_or(|granted| granted < **level)
        })
        .map(|(name, level)| format!("{name}: {}", level.as_str()))
        .collect()
}

/// The App's own permissions as levels; unknown values (`admin`) are skipped.
fn parse_levels(permissions: &BTreeMap<String, String>) -> BTreeMap<String, PermissionLevel> {
    permissions
        .iter()
        .filter_map(|(name, value)| {
            PermissionLevel::parse(value).map(|level| (name.clone(), level))
        })
        .collect()
}

fn installation_scope(installation: &GitHubInstallation) -> String {
    if installation.repository_selection == "all" {
        return format!("all repositories of {}", installation.account.login);
    }
    let repositories = &installation.repositories;
    if repositories.is_empty() {
        return format!("no repositories of {}", installation.account.login);
    }
    let listed: Vec<&str> = repositories
        .iter()
        .take(LISTED_REPOSITORIES)
        .map(String::as_str)
        .collect();
    let more = repositories.len().saturating_sub(LISTED_REPOSITORIES);
    if more > 0 || installation.repositories_truncated {
        format!("{} and more", listed.join(", "))
    } else {
        listed.join(", ")
    }
}

fn installation_settings_url(installation: &GitHubInstallation) -> String {
    installation.settings_url.clone().unwrap_or_else(|| {
        if installation.account.is_organization() {
            format!(
                "https://github.com/organizations/{}/settings/installations/{}",
                installation.account.login, installation.id
            )
        } else {
            format!(
                "https://github.com/settings/installations/{}",
                installation.id
            )
        }
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::super::manifest::EventPermission;
    use super::*;

    fn channel() -> GitHubChannel {
        GitHubChannel {
            name: "github".into(),
            events_path: "/channels/github/events".into(),
            handled: vec!["issue_comment.created".into(), "issues.opened".into()],
            events: vec!["issue_comment".into(), "issues".into()],
            permissions: BTreeMap::from([
                ("issues".to_string(), PermissionLevel::Write),
                ("metadata".to_string(), PermissionLevel::Read),
            ]),
            app_name: "acme-triage".into(),
            repositories: vec![],
            event_permissions: vec![EventPermission {
                name: "issues".into(),
                level: PermissionLevel::Write,
                events: vec!["issue_comment.created".into(), "issues.opened".into()],
            }],
        }
    }

    fn status(value: serde_json::Value) -> GitHubChannelStatus {
        serde_json::from_value(value).unwrap()
    }

    fn healthy() -> serde_json::Value {
        json!({
            "app": {
                "id": 1, "slug": "acme-triage",
                "owner": { "login": "acme", "type": "Organization" },
                "permissions": { "issues": "write", "metadata": "read" },
                "events": ["issue_comment", "issues"]
            },
            "installations": [{
                "id": 9, "account": { "login": "acme", "type": "Organization" },
                "repository_selection": "selected", "repositories": ["acme/api", "acme/web"],
                "permissions": { "issues": "write", "metadata": "read" }
            }]
        })
    }

    #[test]
    fn narrowing_is_a_dropped_permission_or_a_lower_level() {
        let scopes = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let current = scopes(&["contents:write", "issues:read"]);
        assert!(!narrows(&current, &current));
        assert!(!narrows(
            &current,
            &scopes(&["contents:write", "issues:write"])
        ));
        assert!(!narrows(
            &current,
            &scopes(&["contents:write", "issues:read", "pull_requests:read"])
        ));
        assert!(narrows(&current, &scopes(&["issues:read"])));
        assert!(narrows(
            &current,
            &scopes(&["contents:read", "issues:read"])
        ));
        // A token without scopes has all of the App's permissions.
        assert!(narrows(&[], &current));
        assert!(!narrows(&[], &[]));
        assert!(narrows(&current, &[]));
    }

    #[test]
    fn granted_scopes_keep_what_the_installation_covers() {
        let required = BTreeMap::from([
            ("issues".to_string(), PermissionLevel::Write),
            ("metadata".to_string(), PermissionLevel::Read),
            ("pull_requests".to_string(), PermissionLevel::Write),
        ]);
        let granted = BTreeMap::from([
            ("issues".to_string(), "write".to_string()),
            ("metadata".to_string(), "read".to_string()),
            ("pull_requests".to_string(), "read".to_string()),
        ]);
        assert_eq!(
            granted_scopes(&required, &granted),
            vec!["issues:write".to_string(), "metadata:read".to_string()]
        );
    }

    #[test]
    fn healthy_app_reports_ready() {
        let report = build(&channel(), &status(healthy()), None);
        assert_eq!(
            report,
            Report {
                ready: Some(
                    "GitHub channel ready · App acme-triage (acme) · installed on acme/api, acme/web"
                        .into()
                ),
                warnings: vec![],
                notes: vec![],
            }
        );
    }

    #[test]
    fn missing_permissions_and_events_name_the_app_owner_and_link() {
        let mut value = healthy();
        value["app"]["permissions"] = json!({ "issues": "read", "metadata": "read" });
        value["app"]["events"] = json!(["issues"]);
        let report = build(&channel(), &status(value), None);
        assert_eq!(
            report.warnings,
            vec![[
                    "GitHub App acme-triage's permissions do not match this channel.",
                    "",
                    "GitHub has no API to change an App's permissions, so the App owner makes the changes below at",
                    "https://github.com/organizations/acme/settings/apps/acme-triage/permissions",
                    "",
                    "Add (required):",
                    "  issues         read → write  to reply to issue_comment.created, issues.opened",
                    "  issue_comment  event         to receive issue_comment.created",
                ]
                .join("\n")]
        );
        assert!(report.ready.is_some());
    }

    /// `count` user installations of the App, each on older App permissions.
    fn installed_in(count: usize) -> serde_json::Value {
        let mut value = healthy();
        value["app"]["permissions"] =
            json!({ "issues": "write", "metadata": "read", "checks": "read" });
        value["installations"] = (0..count)
            .map(|i| {
                json!({
                    "id": 100 + i, "account": { "login": format!("user{i}"), "type": "User" },
                    "repository_selection": "all",
                    "permissions": { "issues": "write", "metadata": "read" }
                })
            })
            .collect();
        value
    }

    #[test]
    fn many_installations_are_summed_up_not_listed() {
        let mut value = installed_in(5);
        value["installations_truncated"] = json!(true);
        value["installation_requests"] = (0..4)
            .map(|i| json!({ "account": { "login": format!("org{i}"), "type": "Organization" } }))
            .collect();
        let report = build(&channel(), &status(value), None);

        assert_eq!(
            report.ready.as_deref(),
            Some(
                "GitHub channel ready · App acme-triage (acme) · installed on at least 5 accounts (user0, user1, user2 and at least 2 more)"
            )
        );
        assert!(
            report.warnings.contains(
                &"Waiting for owners of 4 accounts to approve installing the App: org0, org1, org2 and 1 more."
                    .to_string()
            ),
            "{:?}",
            report.warnings
        );
        assert!(
            report.warnings.contains(
                &"Owners of at least 5 accounts have not accepted the App's updated permissions (checks: read) yet: user0, user1, user2 and at least 2 more. Each owner accepts them in the installation's settings on GitHub."
                    .to_string()
            ),
            "{:?}",
            report.warnings
        );
        assert!(
            !report
                .warnings
                .iter()
                .any(|w| w.starts_with("An owner of") || w.starts_with("Waiting for an owner")),
            "{:?}",
            report.warnings
        );
    }

    #[test]
    fn installation_requests_past_the_report_are_a_lower_bound() {
        let mut value = healthy();
        value["installation_requests"] = (0..5)
            .map(|i| json!({ "account": { "login": format!("org{i}"), "type": "Organization" } }))
            .collect();
        value["installation_requests_truncated"] = json!(true);
        let report = build(&channel(), &status(value), None);

        assert!(
            report.warnings.contains(
                &"Waiting for owners of at least 5 accounts to approve installing the App: org0, org1, org2 and at least 2 more."
                    .to_string()
            ),
            "{:?}",
            report.warnings
        );
    }

    #[test]
    fn a_few_installations_are_still_listed_one_by_one() {
        let report = build(&channel(), &status(installed_in(3)), None);
        assert_eq!(
            report.ready.as_deref(),
            Some(
                "GitHub channel ready · App acme-triage (acme) · installed on all repositories of user0; all repositories of user1; all repositories of user2"
            )
        );
        assert_eq!(
            report
                .warnings
                .iter()
                .filter(|w| w.starts_with("An owner of user"))
                .count(),
            3,
            "{:?}",
            report.warnings
        );
    }

    #[test]
    fn unaccepted_permissions_ask_the_installation_owner() {
        let mut value = healthy();
        value["app"]["permissions"] =
            json!({ "issues": "write", "metadata": "read", "checks": "read" });
        let report = build(&channel(), &status(value), None);
        assert_eq!(
            report.warnings,
            vec![
                [
                    "GitHub App acme-triage's permissions do not match this channel.",
                    "",
                    "GitHub has no API to change an App's permissions, so the App owner makes the changes below at",
                    "https://github.com/organizations/acme/settings/apps/acme-triage/permissions",
                    "",
                    "Remove or lower (optional: deploy already keeps these out of the agent's token, but the App still holds them):",
                    "  checks  read  unused",
                ]
                .join("\n"),
                "An owner of acme must accept the App's updated permissions (checks: read) at https://github.com/organizations/acme/settings/installations/9"
                    .to_string(),
            ]
        );
        assert!(report.notes.is_empty());
    }

    #[test]
    fn permissions_above_what_the_channel_needs_are_a_warning() {
        let mut value = healthy();
        value["app"]["permissions"] = json!({
            "contents": "write", "issues": "write", "metadata": "read", "pull_requests": "admin"
        });
        let mut narrowed = channel();
        narrowed
            .permissions
            .insert("contents".to_string(), PermissionLevel::Read);
        narrowed
            .permissions
            .insert("pull_requests".to_string(), PermissionLevel::Write);
        let report = build(&narrowed, &status(value), None);
        assert_eq!(
            report.warnings[0],
            [
                "GitHub App acme-triage's permissions do not match this channel.",
                "",
                "GitHub has no API to change an App's permissions, so the App owner makes the changes below at",
                "https://github.com/organizations/acme/settings/apps/acme-triage/permissions",
                "",
                "Remove or lower (optional: deploy already keeps these out of the agent's token, but the App still holds them):",
                "  contents       write → read   the channel needs only read",
                "  pull_requests  admin → write  the channel needs only write",
            ]
            .join("\n")
        );
    }

    #[test]
    fn token_summary_says_why_and_what_is_still_waiting() {
        let mut explained = channel();
        explained
            .permissions
            .insert("contents".to_string(), PermissionLevel::Read);
        explained
            .permissions
            .insert("projects".to_string(), PermissionLevel::Write);
        explained
            .permissions
            .insert("pull_requests".to_string(), PermissionLevel::Write);
        explained.event_permissions.push(EventPermission {
            name: "pull_requests".into(),
            level: PermissionLevel::Write,
            events: vec!["pull_request_review.submitted".into()],
        });
        let scopes = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let reported = status(healthy());
        let installation = &reported.installations[0];

        // The App lacks projects, so its owner adds it before the
        // installation can accept it.
        assert_eq!(
            token_summary(
                &explained,
                &reported,
                installation,
                &scopes(&[
                    "contents:read",
                    "issues:write",
                    "metadata:read",
                    "pull_requests:write"
                ]),
                true
            ),
            [
                "The agent's GitHub token now has:",
                "  contents       read   from app.permissions",
                "  issues         write  to reply to issue_comment.created, issues.opened",
                "  pull_requests  write  to reply to pull_request_review.submitted",
                "",
                "Waiting for GitHub:",
                "  projects       write  from app.permissions",
                "",
                "1. The App owner grants projects (write) at https://github.com/organizations/acme/settings/apps/acme-triage/permissions",
                "2. An owner of acme accepts them at https://github.com/organizations/acme/settings/installations/9",
                "3. Run `mda deploy` again.",
            ]
            .join("\n")
        );

        // The App is not subscribed to every event either: step 1 says so.
        let mut unsubscribed = healthy();
        unsubscribed["app"]["events"] = json!([]);
        let unsubscribed = status(unsubscribed);
        let summary = token_summary(
            &explained,
            &unsubscribed,
            &unsubscribed.installations[0],
            &scopes(&["issues:write", "metadata:read"]),
            false,
        );
        assert!(
            summary.contains("\n1. The App owner grants contents (read), projects (write) and pull_requests (write) and subscribes to the issue_comment and issues events at "),
            "{summary}"
        );

        // The App has issues: write; only the installation has to accept it.
        // A lower level than the channel needs is still waiting.
        let accept_only = [
            "",
            "Waiting for GitHub:",
            "  issues  write  to reply to issue_comment.created, issues.opened",
            "",
            "1. An owner of acme accepts them at https://github.com/organizations/acme/settings/installations/9",
            "2. Run `mda deploy` again.",
        ];
        assert_eq!(
            token_summary(
                &channel(),
                &reported,
                installation,
                &scopes(&["issues:read", "metadata:read"]),
                false
            ),
            [
                &[
                    "The agent's GitHub token has:",
                    "  issues  read   to reply to issue_comment.created, issues.opened",
                ][..],
                &accept_only[..],
            ]
            .concat()
            .join("\n")
        );

        assert_eq!(
            token_summary(
                &channel(),
                &reported,
                installation,
                &scopes(&["metadata:read"]),
                false
            ),
            [
                &["The agent's GitHub token has no permissions beyond reading repository metadata."][..],
                &accept_only[..],
            ]
            .concat()
            .join("\n")
        );
    }

    #[test]
    fn unknown_permission_names_get_a_hint() {
        let mut misnamed = channel();
        for name in ["projects", "pull_request", "zzz"] {
            misnamed
                .permissions
                .insert(name.to_string(), PermissionLevel::Write);
        }
        let report = build(&misnamed, &status(healthy()), None);
        assert_eq!(
            report.warnings[..3],
            [
                "app.permissions names projects, which GitHub does not list as an App permission. Did you mean organization_projects or repository_projects?",
                "app.permissions names pull_request, which GitHub does not list as an App permission. Did you mean pull_requests?",
                "app.permissions names zzz, which GitHub does not list as an App permission.",
            ]
        );
        assert!(build(&channel(), &status(healthy()), None)
            .warnings
            .is_empty());
    }

    #[test]
    fn not_installed_links_the_install_page() {
        let mut value = healthy();
        value["installations"] = json!([]);
        value["installation_requests"] =
            json!([{ "account": { "login": "acme" }, "requester": { "login": "alice" } }]);
        let report = build(&channel(), &status(value), None);
        assert_eq!(report.ready, None);
        assert_eq!(
            report.warnings,
            vec![
                "Waiting for an owner of acme to approve the installation requested by alice."
                    .to_string(),
                "GitHub App acme-triage is not installed yet. Install it on your repositories at https://github.com/apps/acme-triage/installations/new"
                    .to_string(),
            ]
        );
    }

    #[test]
    fn repositories_outside_the_installation_are_reported() {
        let mut limited = channel();
        limited.repositories = vec!["acme/api".into(), "acme/docs".into()];
        let report = build(&limited, &status(healthy()), None);
        assert_eq!(
            report.warnings,
            vec![
                "The installation on acme does not include acme/docs, which this channel is limited to. Add them at https://github.com/organizations/acme/settings/installations/9"
                    .to_string()
            ]
        );

        let mut value = healthy();
        value["installations"][0]["repository_selection"] = json!("all");
        assert!(build(&limited, &status(value), None).warnings.is_empty());

        let mut value = healthy();
        value["installations"][0]["repositories_truncated"] = json!(true);
        assert!(build(&limited, &status(value), None).warnings.is_empty());

        limited.repositories = vec!["other/api".into()];
        assert!(build(&limited, &status(healthy()), None).warnings[0].contains("other/api"));
    }

    #[test]
    fn suspended_installations_are_not_ready() {
        let mut value = healthy();
        value["installations"][0]["suspended_at"] = json!("2026-10-01T00:00:00Z");
        let report = build(&channel(), &status(value), None);
        assert_eq!(report.ready, None);
        assert_eq!(
            report.warnings,
            vec![
                "The installation on acme is suspended, so it receives no events. Unsuspend it at https://github.com/organizations/acme/settings/installations/9"
                    .to_string()
            ]
        );
    }

    /// A public App: the agent's installation (9, all of acme's
    /// repositories) and `others` user installations, the first `behind` of
    /// which have not accepted `checks: read`, plus `requests` pending ones.
    fn public_app(others: usize, behind: usize, requests: usize) -> serde_json::Value {
        let mut value = healthy();
        value["app"]["permissions"] =
            json!({ "checks": "read", "issues": "write", "metadata": "read" });
        value["installations"][0]["repository_selection"] = json!("all");
        value["installations"][0]["permissions"] = value["app"]["permissions"].clone();
        for index in 0..others {
            let permissions = if index < behind {
                json!({ "issues": "write", "metadata": "read" })
            } else {
                value["app"]["permissions"].clone()
            };
            value["installations"].as_array_mut().unwrap().push(json!({
                "id": 100 + index, "account": { "login": format!("user{index}"), "type": "User" },
                "repository_selection": "selected", "repositories": [format!("user{index}/app")],
                "permissions": permissions
            }));
        }
        value["installation_requests"] = (0..requests)
            .map(|index| {
                json!({
                    "account": { "login": format!("org{index}"), "type": "Organization" },
                    "requester": { "login": format!("dev{index}") }
                })
            })
            .collect();
        value
    }

    #[test]
    fn many_installations_are_counted_and_the_agents_own_is_named() {
        let report = build(&channel(), &status(public_app(112, 100, 45)), Some(9));
        assert_eq!(
            report.ready.unwrap(),
            "GitHub channel ready · App acme-triage (acme) · this agent uses all repositories of acme · also installed on 112 other accounts (user0, user1, user2 and 109 more)"
        );
        // Only the permission table is left as a warning: nothing is wrong
        // with the agent's own installation.
        assert_eq!(report.warnings.len(), 1);
        assert!(report.warnings[0].contains("  checks  read  unused"));
        assert_eq!(
            report.notes,
            vec![
                "Waiting for owners of 45 accounts to approve installing the App: org0, org1, org2 and 42 more.".to_string(),
                "Owners of 100 other accounts have not accepted the App's updated permissions (checks: read) yet: user0, user1, user2 and 97 more. Each owner accepts them in the installation's settings on GitHub.".to_string(),
            ]
        );
    }

    #[test]
    fn the_agents_own_installation_keeps_its_warning_in_full() {
        let mut value = public_app(112, 100, 0);
        value["installations"][0]["permissions"] = json!({ "issues": "write", "metadata": "read" });
        let report = build(&channel(), &status(value), Some(9));
        assert_eq!(
            report.warnings[1],
            "An owner of acme must accept the App's updated permissions (checks: read) at https://github.com/organizations/acme/settings/installations/9"
        );
        assert_eq!(report.warnings.len(), 2);
        assert!(report.notes[0].starts_with("Owners of 100 other accounts "));

        // Suspended, the agent's installation is not ready, and the others
        // stay warnings: the agent has no installation that works.
        let mut value = public_app(5, 0, 0);
        value["installations"][0]["suspended_at"] = json!("2026-10-01T00:00:00Z");
        let report = build(&channel(), &status(value), Some(9));
        assert_eq!(
            report.warnings[1],
            "The installation on acme is suspended, so it receives no events. Unsuspend it at https://github.com/organizations/acme/settings/installations/9"
        );
        assert_eq!(
            report.ready.unwrap(),
            "GitHub channel ready · App acme-triage (acme) · installed on 5 accounts (user0, user1, user2 and 2 more)"
        );
    }

    #[test]
    fn many_installations_of_an_unknown_agent_installation_stay_warnings() {
        let report = build(&channel(), &status(public_app(112, 100, 45)), None);
        assert_eq!(
            report.ready.unwrap(),
            "GitHub channel ready · App acme-triage (acme) · installed on 113 accounts (acme, user0, user1 and 110 more)"
        );
        assert_eq!(
            report.warnings[1..],
            [
                "Waiting for owners of 45 accounts to approve installing the App: org0, org1, org2 and 42 more.",
                "Owners of 100 accounts have not accepted the App's updated permissions (checks: read) yet: user0, user1, user2 and 97 more. Each owner accepts them in the installation's settings on GitHub.",
            ]
        );
        assert!(report.notes.is_empty());

        // A recorded installation GitHub no longer reports names nothing.
        assert_eq!(
            build(&channel(), &status(public_app(112, 100, 45)), Some(1)),
            build(&channel(), &status(public_app(112, 100, 45)), None)
        );
    }

    #[test]
    fn a_short_list_is_printed_in_full() {
        let report = build(&channel(), &status(public_app(2, 1, 1)), Some(9));
        assert_eq!(
            report.ready.unwrap(),
            "GitHub channel ready · App acme-triage (acme) · this agent uses all repositories of acme · also installed on user0/app; user1/app"
        );
        assert_eq!(
            report.notes,
            vec![
                "Waiting for an owner of org0 to approve the installation requested by dev0.".to_string(),
                "An owner of user0 must accept the App's updated permissions (checks: read) at https://github.com/settings/installations/100".to_string(),
            ]
        );

        // Without the agent's installation, the same lines are warnings.
        let report = build(&channel(), &status(public_app(2, 1, 1)), None);
        assert_eq!(
            report.ready.unwrap(),
            "GitHub channel ready · App acme-triage (acme) · installed on all repositories of acme; user0/app; user1/app"
        );
        assert_eq!(
            report.warnings[1..],
            [
                "Waiting for an owner of org0 to approve the installation requested by dev0.",
                "An owner of user0 must accept the App's updated permissions (checks: read) at https://github.com/settings/installations/100",
            ]
        );
    }

    #[test]
    fn installation_scope_wording() {
        let mut value = healthy();
        value["installations"][0]["repository_selection"] = json!("all");
        assert_eq!(
            build(&channel(), &status(value), None).ready.unwrap(),
            "GitHub channel ready · App acme-triage (acme) · installed on all repositories of acme"
        );

        let mut value = healthy();
        value["installations"][0]["repositories"] =
            json!(["a/1", "a/2", "a/3", "a/4", "a/5", "a/6"]);
        assert!(build(&channel(), &status(value), None)
            .ready
            .unwrap()
            .ends_with("installed on a/1, a/2, a/3, a/4, a/5 and more"));

        let mut value = healthy();
        value["app"]["owner"] = json!({ "login": "alice", "type": "User" });
        value["installations"][0]["account"] = json!({ "login": "alice", "type": "User" });
        value["installations"][0]["suspended_at"] = json!("x");
        assert!(build(&channel(), &status(value), None).warnings[0]
            .ends_with("https://github.com/settings/installations/9"));
    }
}
