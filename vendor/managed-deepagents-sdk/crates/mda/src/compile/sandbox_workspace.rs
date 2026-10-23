use std::fs;
use std::io::Cursor;
use std::path::Path;

use anyhow::{bail, Context, Result};
use base64::Engine as _;
use flate2::{write::GzEncoder, Compression};
use walkdir::WalkDir;

use crate::project::{self, Project};

use super::discovery::read_setup_script;

const WORKSPACE_DIR: &str = "sandbox/workspace";
const MAX_WORKSPACE_BYTES: u64 = 10 * 1024 * 1024;

pub(crate) fn read_sandbox_setup(project: &Project) -> Result<Option<String>> {
    let setup = read_setup_script(project)?;
    let Some(archive) = workspace_archive(&project.root)? else {
        return Ok(setup);
    };
    let encoded = base64::engine::general_purpose::STANDARD.encode(archive);
    Ok(Some(format!(
        "mkdir -p /workspace && (set -o pipefail; printf '%s' '{encoded}' | base64 -d | tar -xz -C /workspace) || exit 1\n{}",
        setup.unwrap_or_default()
    )))
}

fn workspace_archive(root: &Path) -> Result<Option<Vec<u8>>> {
    let workspace = root.join(WORKSPACE_DIR);
    if !workspace.try_exists()? {
        return Ok(None);
    }
    for directory in [root.join("sandbox"), workspace.clone()] {
        if fs::symlink_metadata(&directory)?.file_type().is_symlink() {
            bail!(
                "sandbox workspace must not contain symlinks: {}",
                directory.display()
            );
        }
    }
    let mut archive = tar::Builder::new(GzEncoder::new(Vec::new(), Compression::default()));
    let mut total = 0u64;
    let mut count = 0;
    for entry in WalkDir::new(&workspace)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|entry| !project::is_ignored_dir(entry.path()))
    {
        let entry = entry.with_context(|| format!("failed to read {}", workspace.display()))?;
        let path = entry.path();
        if entry.file_type().is_symlink() {
            bail!(
                "sandbox workspace must not contain symlinks: {}",
                path.display()
            );
        }
        if entry.file_type().is_dir() {
            continue;
        }
        if !entry.file_type().is_file() {
            bail!(
                "sandbox workspace requires regular files: {}",
                path.display()
            );
        }
        let relative = path.strip_prefix(&workspace)?;
        if project::is_ignored_file(relative) {
            continue;
        }
        let metadata = entry.metadata()?;
        total = total.saturating_add(metadata.len());
        if total > MAX_WORKSPACE_BYTES {
            bail!("sandbox workspace exceeds the 10 MiB prototype limit");
        }
        let bytes = fs::read(path).with_context(|| format!("failed to read {}", path.display()))?;
        let mut header = tar::Header::new_gnu();
        header.set_size(bytes.len() as u64);
        header.set_mode(file_mode(&metadata));
        header.set_cksum();
        archive.append_data(&mut header, relative, Cursor::new(bytes))?;
        count += 1;
    }
    if count == 0 {
        return Ok(None);
    }
    Ok(Some(archive.into_inner()?.finish()?))
}

#[cfg(unix)]
fn file_mode(metadata: &fs::Metadata) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    if metadata.permissions().mode() & 0o111 != 0 {
        0o755
    } else {
        0o644
    }
}

#[cfg(not(unix))]
fn file_mode(_metadata: &fs::Metadata) -> u32 {
    0o644
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::deploy::helpers::recipe_digest;
    use crate::project::Language;
    use std::path::PathBuf;
    use std::process::Command;

    fn project(root: &Path) -> Project {
        Project {
            root: root.to_path_buf(),
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: if root.join("sandbox/setup.sh").is_file() {
                vec![PathBuf::from("sandbox/setup.sh")]
            } else {
                vec![]
            },
        }
    }

    #[test]
    fn seeds_are_available_before_setup_and_invalidate_the_recipe() {
        let root = tempfile::tempdir().unwrap();
        let workspace = root.path().join(WORKSPACE_DIR);
        fs::create_dir_all(workspace.join("nested")).unwrap();
        fs::write(workspace.join("nested/data.bin"), [0, 255, 42]).unwrap();
        fs::write(workspace.join(".env"), "must not ship").unwrap();
        fs::write(workspace.join("nested/hello.sh"), "#!/bin/sh\nprintf ready").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(
                workspace.join("nested/hello.sh"),
                fs::Permissions::from_mode(0o755),
            )
            .unwrap();
        }
        let output = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("sandbox/setup.sh"),
            format!(
                "test -f '{}/nested/data.bin' && test ! -e '{}/.env' && sh '{}/nested/hello.sh'\n",
                output.path().display(),
                output.path().display(),
                output.path().display()
            ),
        )
        .unwrap();
        let project = project(root.path());
        let script = read_sandbox_setup(&project).unwrap().unwrap();
        assert_eq!(script, read_sandbox_setup(&project).unwrap().unwrap());
        let runnable = script.replace("/workspace", &format!("'{}'", output.path().display()));
        let result = Command::new("bash")
            .arg("-e")
            .arg("-c")
            .arg(runnable)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        assert_eq!(result.stdout, b"ready");
        assert_eq!(
            fs::read(output.path().join("nested/data.bin")).unwrap(),
            [0, 255, 42]
        );
        #[cfg(unix)]
        assert_eq!(
            file_mode(&fs::metadata(output.path().join("nested/hello.sh")).unwrap()),
            0o755
        );
        let digest = |script: &str| recipe_digest(script.as_bytes(), None, None, None, None);
        fs::write(workspace.join("nested/data.bin"), [1, 2, 3]).unwrap();
        assert_ne!(
            digest(&script),
            digest(&read_sandbox_setup(&project).unwrap().unwrap())
        );
        let changed = read_sandbox_setup(&project).unwrap().unwrap();
        fs::rename(
            workspace.join("nested/data.bin"),
            workspace.join("nested/renamed.bin"),
        )
        .unwrap();
        let renamed = read_sandbox_setup(&project).unwrap().unwrap();
        assert_ne!(digest(&changed), digest(&renamed));
        fs::remove_file(workspace.join("nested/renamed.bin")).unwrap();
        assert_ne!(
            digest(&renamed),
            digest(&read_sandbox_setup(&project).unwrap().unwrap())
        );
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinks_outside_the_seed_tree() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join(WORKSPACE_DIR)).unwrap();
        std::os::unix::fs::symlink(
            "/etc/passwd",
            root.path().join(WORKSPACE_DIR).join("secret"),
        )
        .unwrap();
        assert!(read_sandbox_setup(&project(root.path()))
            .unwrap_err()
            .to_string()
            .contains("symlinks"));
    }
}
