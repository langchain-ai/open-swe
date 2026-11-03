//! Build and publish customer-managed Agent Server images.

use std::path::Path;
use std::process::{Command, Output};

use anyhow::{bail, Context, Result};

use crate::child;
use crate::dependencies;
use crate::error::child_failed;
use crate::langsmith::is_self_hosted_host_backend_url;
use crate::project::Language;
use crate::toolchain;

const DIGEST_PREFIX: &str = "@sha256:";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ImageSource {
    InternalSource,
    CustomerRegistry(PushTarget),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PushTarget {
    repository: String,
    tagged_image: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CommandSpec {
    program: String,
    args: Vec<String>,
}

/// Set to `1` when the host-backend builds images from the uploaded source
/// even though it is served below `/api-host`, as the local stack in
/// `tools/local-stack` does. `mda-local env` sets it.
const HOST_BUILDS_IMAGES_ENV: &str = "MDA_HOST_BUILDS_IMAGES";

pub(crate) fn requires_external_image(host_url: &str) -> bool {
    requires_external_image_with(
        host_url,
        std::env::var(HOST_BUILDS_IMAGES_ENV).ok().as_deref(),
    )
}

fn requires_external_image_with(host_url: &str, host_builds_images: Option<&str>) -> bool {
    let opted_out = host_builds_images
        .is_some_and(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true"));
    !opted_out && is_self_hosted_host_backend_url(host_url)
}

pub(crate) fn select_image_source(self_hosted: bool, push_to: Option<&str>) -> Result<ImageSource> {
    match push_to {
        Some(value) if self_hosted => Ok(ImageSource::CustomerRegistry(parse_push_target(value)?)),
        Some(_) => bail!("--push-to is only supported for self-hosted LangSmith deployments"),
        None if self_hosted => bail!(
            "self-hosted LangSmith requires a customer-managed image; pass --push-to <repository[:tag]>"
        ),
        None => Ok(ImageSource::InternalSource),
    }
}

fn parse_push_target(value: &str) -> Result<PushTarget> {
    let value = value.trim();
    if value.contains('@') || value.len() > 255 {
        bail!("--push-to must be a Docker repository with an optional tag, such as registry.example.com/team/agent:latest");
    }

    let last_slash = value.rfind('/');
    let tag_separator = value
        .rfind(':')
        .filter(|index| last_slash.is_none_or(|slash| *index > slash));
    let (repository, tagged_image) = match tag_separator {
        Some(index) => (&value[..index], value.to_string()),
        None => (value, format!("{value}:latest")),
    };
    let tag = tag_separator.map(|index| &value[index + 1..]);
    if !valid_repository(repository) || tag.is_some_and(|tag| !valid_tag(tag)) {
        bail!("--push-to must be a valid Docker repository with an optional tag");
    }
    Ok(PushTarget {
        repository: repository.to_string(),
        tagged_image,
    })
}

fn valid_repository(value: &str) -> bool {
    !value.is_empty()
        && !value.contains("//")
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(byte, b'.' | b'_' | b'-' | b'/' | b':')
        })
        && value
            .rsplit_once('/')
            .map_or_else(|| !value.contains(':'), |(_, name)| !name.contains(':'))
        && value
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .as_bytes()
            .last()
            .is_some_and(u8::is_ascii_alphanumeric)
}

fn valid_tag(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
        && !value.starts_with(['.', '-'])
}

pub(crate) fn build_and_push(
    language: Language,
    build_dir: &Path,
    target: &PushTarget,
) -> Result<String> {
    run(
        build_command(language, target),
        build_dir,
        "build the deployment image",
    )?;
    run(
        CommandSpec {
            program: "docker".to_string(),
            args: vec!["push".to_string(), target.tagged_image.clone()],
        },
        build_dir,
        "push the deployment image",
    )?;

    let output = run(
        CommandSpec {
            program: "docker".to_string(),
            args: vec![
                "image".to_string(),
                "inspect".to_string(),
                "--format".to_string(),
                "{{json .RepoDigests}}".to_string(),
                target.tagged_image.clone(),
            ],
        },
        build_dir,
        "inspect the pushed deployment image",
    )?;
    resolve_pushed_digest(target, &output.stdout)
}

fn build_command(language: Language, target: &PushTarget) -> CommandSpec {
    build_command_for_arch(language, target, std::env::consts::ARCH)
}

fn build_command_for_arch(language: Language, target: &PushTarget, arch: &str) -> CommandSpec {
    let (program, mut args) = match language {
        Language::TypeScript => {
            let dep = dependencies::TYPESCRIPT_DEV_SERVER_CLI;
            (
                "npx".to_string(),
                vec![
                    "--yes".to_string(),
                    format!("{}@{}", dep.name, dep.version),
                    "build".to_string(),
                    "-t".to_string(),
                    target.tagged_image.clone(),
                ],
            )
        }
        Language::Python => {
            let dep = dependencies::PYTHON_DEV_SERVER_CLI;
            (
                "uv".to_string(),
                vec![
                    "run".to_string(),
                    "--no-project".to_string(),
                    "--with".to_string(),
                    format!("{}{}", dep.name, dep.version),
                    "langgraph".to_string(),
                    "build".to_string(),
                    "-t".to_string(),
                    target.tagged_image.clone(),
                ],
            )
        }
    };
    if arch != "x86_64" {
        args.extend(["--platform".to_string(), "linux/amd64".to_string()]);
    }
    CommandSpec { program, args }
}

fn run(spec: CommandSpec, cwd: &Path, action: &str) -> Result<Output> {
    let mut command = match spec.program.as_str() {
        "npx" => toolchain::npx(),
        "uv" => toolchain::uv(),
        program => Command::new(program),
    };
    let output = child::output(command.args(&spec.args).current_dir(cwd))
        .with_context(|| format!("failed to {action}: could not run {}", spec.program))?;
    if !output.status.success() {
        return Err(child_failed(&output, format!("failed to {action}")));
    }
    Ok(output)
}

fn resolve_pushed_digest(target: &PushTarget, stdout: &[u8]) -> Result<String> {
    let digests: Vec<String> =
        serde_json::from_slice(stdout).context("docker returned an invalid image digest list")?;
    let prefix = format!("{}{}", target.repository, DIGEST_PREFIX);
    let digest = digests
        .into_iter()
        .find(|digest| digest.starts_with(&prefix))
        .ok_or_else(|| anyhow::anyhow!("pushed image has no digest for {}", target.repository))?;
    validate_image_uri(&digest)
}

fn validate_image_uri(value: &str) -> Result<String> {
    let Some((repository, digest)) = value.rsplit_once(DIGEST_PREFIX) else {
        bail!("pushed image did not resolve to an immutable sha256 digest");
    };
    if repository.is_empty()
        || repository.contains('@')
        || digest.len() != 64
        || !digest.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        bail!("pushed image did not resolve to an immutable sha256 digest");
    }
    Ok(value.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn self_hosted_requires_push_to() {
        let err = select_image_source(true, None).unwrap_err();
        assert!(err.to_string().contains("--push-to"));
    }

    #[test]
    fn api_host_path_identifies_self_hosted_deployments() {
        assert!(requires_external_image(
            "http://langsmith.localhost/api-host"
        ));
    }

    #[test]
    fn local_stack_host_requires_an_external_image_without_opt_out() {
        assert!(requires_external_image_with(
            "http://localhost:21980/api-host",
            None
        ));
    }

    #[test]
    fn host_builds_images_opt_out_skips_external_image_for_local_stack() {
        assert!(!requires_external_image_with(
            "http://localhost:21980/api-host",
            Some("1")
        ));
    }

    #[test]
    fn host_builds_images_ignores_values_other_than_one_or_true() {
        for value in ["", "0", "false", "yes"] {
            assert!(
                requires_external_image_with("http://localhost:21980/api-host", Some(value)),
                "{value:?} must not opt out"
            );
        }
    }

    #[test]
    fn cloud_host_does_not_require_an_external_image() {
        assert!(!requires_external_image("https://api.host.langchain.com"));
    }

    #[test]
    fn cloud_defaults_to_internal_source() {
        assert_eq!(
            select_image_source(false, None).unwrap(),
            ImageSource::InternalSource
        );
    }

    #[test]
    fn cloud_rejects_push_to() {
        assert!(select_image_source(false, Some("example.com/team/agent")).is_err());
    }

    #[test]
    fn push_target_defaults_to_latest_and_allows_registry_ports() {
        let target = parse_push_target("localhost:5000/team/agent").unwrap();
        assert_eq!(target.repository, "localhost:5000/team/agent");
        assert_eq!(target.tagged_image, "localhost:5000/team/agent:latest");
    }

    #[test]
    fn push_target_preserves_explicit_tag() {
        let target = parse_push_target("registry.example/team/agent:preview").unwrap();
        assert_eq!(target.repository, "registry.example/team/agent");
        assert_eq!(target.tagged_image, "registry.example/team/agent:preview");
    }

    #[test]
    fn push_target_rejects_a_digest() {
        assert!(parse_push_target(&format!("example/agent@sha256:{}", "a".repeat(64))).is_err());
    }

    #[test]
    fn push_target_rejects_invalid_repository_and_tag_shapes() {
        for value in [
            "registry.example/Team/agent",
            "example/agent::tag",
            "example/agent:-tag",
        ] {
            assert!(parse_push_target(value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn build_commands_use_language_specific_cli() {
        let target = parse_push_target("example/agent:test").unwrap();
        let ts = build_command_for_arch(Language::TypeScript, &target, "x86_64");
        assert_eq!(ts.program, "npx");
        assert!(ts
            .args
            .ends_with(&["build".into(), "-t".into(), "example/agent:test".into()]));
        let py = build_command_for_arch(Language::Python, &target, "x86_64");
        assert_eq!(py.program, "uv");
        let dep = dependencies::PYTHON_DEV_SERVER_CLI;
        assert_eq!(
            py.args,
            vec![
                "run".to_string(),
                "--no-project".to_string(),
                "--with".to_string(),
                format!("{}{}", dep.name, dep.version),
                "langgraph".to_string(),
                "build".to_string(),
                "-t".to_string(),
                "example/agent:test".to_string(),
            ]
        );
    }

    #[test]
    fn non_amd64_build_targets_linux_amd64() {
        let target = parse_push_target("example/agent:test").unwrap();
        let command = build_command_for_arch(Language::TypeScript, &target, "aarch64");
        assert!(command.args.ends_with(&[
            "example/agent:test".into(),
            "--platform".into(),
            "linux/amd64".into()
        ]));
    }

    #[test]
    fn digest_must_match_requested_repository() {
        let target = parse_push_target("example/agent:preview").unwrap();
        let wanted = format!("example/agent@sha256:{}", "a".repeat(64));
        let other = format!("other/agent@sha256:{}", "b".repeat(64));
        let stdout = serde_json::to_vec(&vec![other, wanted.clone()]).unwrap();
        assert_eq!(resolve_pushed_digest(&target, &stdout).unwrap(), wanted);
    }
}
