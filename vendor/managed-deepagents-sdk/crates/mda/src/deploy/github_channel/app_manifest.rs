//! The github.com pages the setup flow links to.

/// Where a new App is registered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum AppOwner {
    Organization(String),
    /// Whoever is signed in to GitHub in the browser.
    Personal,
}

/// Page where an App's installer picks repositories.
pub(crate) fn install_url(slug: &str) -> String {
    format!("https://github.com/apps/{slug}/installations/new")
}

/// The App's "Permissions & events" settings page.
pub(crate) fn app_permissions_url(owner: &str, owner_is_org: bool, slug: &str) -> String {
    if owner_is_org {
        format!("https://github.com/organizations/{owner}/settings/apps/{slug}/permissions")
    } else {
        format!("https://github.com/settings/apps/{slug}/permissions")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn github_pages() {
        assert_eq!(
            install_url("acme-triage"),
            "https://github.com/apps/acme-triage/installations/new"
        );
        assert_eq!(
            app_permissions_url("acme", true, "acme-triage"),
            "https://github.com/organizations/acme/settings/apps/acme-triage/permissions"
        );
        assert_eq!(
            app_permissions_url("alice", false, "acme-triage"),
            "https://github.com/settings/apps/acme-triage/permissions"
        );
    }
}
