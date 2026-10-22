//! Bake git SHA + commit date into the `mda` binary for `mda --version`.
//!
//! Honors `MDA_GIT_SHA` / `MDA_GIT_DATE` when set (release CI usually injects
//! them) so builds without a usable `.git` tree still get correct metadata.
//! Falls back to probing the local checkout, then to `unknown`.
//! Also bakes the public PostHog ingestion key into release builds so the CLI
//! can send telemetry without requiring user configuration. `MDA_POSTHOG_API_KEY`
//! must be a GitHub Actions repository *variable*, not a secret: it is a public
//! write-only ingestion token, and `${{ vars.* }}` resolves empty for a secret,
//! which would ship releases that silently send nothing.

use std::process::Command;

fn main() {
    let posthog_api_key = std::env::var("MDA_POSTHOG_API_KEY")
        .unwrap_or_default()
        .trim()
        .to_string();
    let sha = std::env::var("MDA_GIT_SHA")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(|s| shorten_sha(s.trim()))
        .or_else(detect_git_sha)
        .unwrap_or_else(|| "unknown".to_string());

    let date = std::env::var("MDA_GIT_DATE")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(|s| s.trim().to_string())
        .or_else(detect_commit_date)
        .unwrap_or_else(|| "unknown".to_string());

    println!("cargo:rustc-env=MDA_POSTHOG_API_KEY={posthog_api_key}");
    println!("cargo:rustc-env=MDA_GIT_SHA={sha}");
    println!("cargo:rustc-env=MDA_GIT_DATE={date}");
    println!("cargo:rerun-if-env-changed=MDA_POSTHOG_API_KEY");
    println!("cargo:rerun-if-env-changed=MDA_GIT_SHA");
    println!("cargo:rerun-if-env-changed=MDA_GIT_DATE");
    // Keep the baked SHA/date fresh across local commits.
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/refs/heads");
}

fn shorten_sha(sha: &str) -> String {
    let short: String = sha.chars().take(7).collect();
    if short.is_empty() {
        "unknown".to_string()
    } else {
        short
    }
}

fn detect_git_sha() -> Option<String> {
    let output = git(&["rev-parse", "--short=7", "HEAD"])?;
    let sha = output.trim();
    if sha.is_empty() {
        None
    } else {
        Some(sha.to_string())
    }
}

fn detect_commit_date() -> Option<String> {
    // `%cs` is committer date in YYYY-MM-DD (git ≥ 2.25).
    let output = git(&["show", "-s", "--format=%cs", "HEAD"])?;
    let date = output.trim();
    if date.is_empty() {
        None
    } else {
        Some(date.to_string())
    }
}

fn git(args: &[&str]) -> Option<String> {
    let output = Command::new("git").args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}
