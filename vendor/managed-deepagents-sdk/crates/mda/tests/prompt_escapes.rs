//! Every interactive prompt needs a way to be answered without a terminal.
//!
//! Coding agents and CI run `mda` with stdin redirected, so a prompt that only a
//! human at a TTY can answer wedges the workflow. This test inventories every
//! `.interact()` call in the CLI and pins each one to the flag, env var, or
//! guard that answers it unattended. Adding a prompt fails here until its
//! escape is recorded below — and, if it has none, until one exists.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

/// `(file, prompts in it, how a non-interactive caller answers them)`.
const PROMPTS: &[(&str, usize, &str)] = &[
    (
        "chat/mod.rs",
        6,
        "interactive-only command: message, tool-review, per-call tool-approval, \
         edited-arguments, reason-or-answer, and interrupt-reply prompts",
    ),
    (
        "coding_agent.rs",
        2,
        "only reached with `-i`, which is rejected without a terminal",
    ),
    (
        "connection/mod.rs",
        2,
        "browser hand-off is skipped without a terminal; delete takes --yes",
    ),
    ("delete/mod.rs", 1, "--yes; refuses without a terminal"),
    (
        "deploy/channel_app.rs",
        1,
        "the try-again-or-skip prompt after a failed GitHub or Linear app setup step is \
         only asked in a terminal; without one, the failure ends the deploy",
    ),
    (
        "deploy/github_channel/mod.rs",
        10,
        "GitHub App setup prompts run only in an interactive terminal; without one, \
         MDA_GITHUB_APP_OWNER picks the App's owner (deploy fails without it), deploy prints \
         the setup link instead of opening a browser, GH_WEBHOOK_SECRET supplies a \
         brought App's webhook secret, and a brought App whose webhook points elsewhere \
         fails the deploy with how to change it on GitHub. Bringing an existing App, with \
         moving it from another deployment and picking one of its installations, is \
         offered only in a terminal; without one, deploy creates a new App, and a brought \
         App without exactly one installation fails with how to finish it in a terminal",
    ),
    (
        "deploy/linear_channel/mod.rs",
        5,
        "Linear app setup prompts run only in an interactive terminal: MDA_LINEAR_WORKSPACE \
         answers the workspace prompt, and without a terminal MDA_LINEAR_CLIENT_ID, \
         MDA_LINEAR_CLIENT_SECRET and MDA_LINEAR_WEBHOOK_SECRET replace the credential \
         prompts (deploy fails with that hint and the create-app link without them), and \
         MDA_LINEAR_WEBHOOK_SECRET alone answers the signing-secret prompt of an installed \
         app; a \
         failed setup fails the deploy instead of offering to try again; the redirect URI \
         confirmation is only asked in a terminal, without one deploy prints the URI to add \
         and goes on",
    ),
    (
        "deploy/mod.rs",
        3,
        "collision overwrite takes --yes; Context Hub conflicts take --context-strategy; \
         API key comes from LANGSMITH_API_KEY",
    ),
    (
        "deploy/trigger_channels.rs",
        1,
        "browser hand-off only shown in an interactive terminal",
    ),
    (
        "secret_input.rs",
        1,
        "--secret-from-env, --secret-from-file, or stdin",
    ),
];

fn count_prompts(dir: &Path, root: &Path, found: &mut BTreeMap<String, usize>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            count_prompts(&path, root, found);
            continue;
        }
        let relative = path
            .strip_prefix(root)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        // Test modules drive prompt themes, not real prompts.
        if path.extension().is_none_or(|ext| ext != "rs") || relative.ends_with("tests.rs") {
            continue;
        }
        // The whole file counts: production code may follow an inline test module.
        let count = fs::read_to_string(&path)
            .unwrap()
            .matches(".interact()")
            .count();
        if count > 0 {
            found.insert(relative, count);
        }
    }
}

#[test]
fn every_prompt_has_a_non_interactive_escape() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut found = BTreeMap::new();
    count_prompts(&src, &src, &mut found);

    let recorded: BTreeMap<String, usize> = PROMPTS
        .iter()
        .map(|(file, count, _)| ((*file).to_string(), *count))
        .collect();

    assert_eq!(
        found,
        recorded,
        "interactive prompts changed. Give every new prompt a flag or env var that \
         answers it without a terminal (and fail with that hint when stdin is not a \
         TTY), then record it in PROMPTS in {}",
        file!()
    );
}
