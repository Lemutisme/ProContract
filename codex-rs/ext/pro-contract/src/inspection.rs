//! Host-granted computation over immutable evidence. Uses the existing isolated check runtime;
//! the worker supplies code and source IDs, never mounts, credentials or host executable paths.

use std::collections::BTreeSet;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::Component;
use std::path::Path;
use std::time::Duration;

use codex_pro_contract::Digest;
use serde_json::json;

use crate::CheckEnvironment;
use crate::ReviewExecution;
use crate::digest_of;

pub(crate) const SOURCE_LAYOUT: &str = "selected_relative_files_v1";
pub(crate) const SOURCE_MANIFEST: &str = "/sources/manifest.json";

pub(crate) struct Source {
    pub id: String,
    pub subject: Digest,
    pub captured_path: String,
    pub bytes: Vec<u8>,
}

pub(crate) struct SourcePaths {
    pub legacy: String,
    pub named: String,
}

/// Names come from the frozen manifest, never from an execution-supplied path.
pub(crate) fn source_paths(id: &str, captured_path: &str) -> Result<SourcePaths, String> {
    let (kind, number) = id.split_once(':').ok_or("invalid inspection source ID")?;
    let index = number
        .parse::<usize>()
        .map_err(|_| "invalid inspection source ID")?;
    let namespace = match kind {
        "file" => "current",
        "baseline" => "baseline",
        _ => return Err("invalid inspection source kind".into()),
    };
    if id != format!("{kind}:{index}") {
        return Err("noncanonical inspection source ID".into());
    }
    if captured_path.contains('\0')
        || captured_path
            .split('/')
            .any(|part| matches!(part, "" | "." | ".."))
        || Path::new(captured_path)
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("inspection source needs a canonical captured relative path".into());
    }
    Ok(SourcePaths {
        legacy: format!("/evidence/{kind}-{index}"),
        named: format!("/sources/{namespace}/{captured_path}"),
    })
}

/// Validate the complete selected layout before reserving an execution.
pub(crate) fn validate_paths(paths: &[SourcePaths]) -> Result<(), String> {
    let mut legacy = BTreeSet::new();
    let mut named = BTreeSet::new();
    for path in paths {
        if !legacy.insert(&path.legacy) || !named.insert(Path::new(&path.named)) {
            return Err("duplicate inspection source mapping".into());
        }
    }
    for path in &named {
        if path
            .ancestors()
            .skip(1)
            .any(|parent| named.contains(parent))
        {
            return Err("inspection source file conflicts with a parent directory".into());
        }
    }
    Ok(())
}

pub(crate) struct Computation<'a> {
    pub subject: Digest,
    pub requirements: &'a [String],
    pub sources: &'a [Source],
    pub python: &'a str,
    pub remaining: Duration,
}

async fn readonly(path: &Path) -> Result<(), String> {
    let mut permissions = tokio::fs::metadata(path)
        .await
        .map_err(|e| e.to_string())?
        .permissions();
    #[cfg(unix)]
    permissions.set_mode(0o444);
    #[cfg(not(unix))]
    permissions.set_readonly(true);
    tokio::fs::set_permissions(path, permissions)
        .await
        .map_err(|e| e.to_string())
}

pub(crate) async fn compute(
    env: &CheckEnvironment,
    grant: &ReviewExecution,
    directory: &Path,
    request: Computation<'_>,
) -> Result<serde_json::Value, String> {
    let Computation {
        subject,
        requirements,
        sources,
        python,
        remaining,
    } = request;
    let started = std::time::Instant::now();
    let paths = sources
        .iter()
        .map(|source| source_paths(&source.id, &source.captured_path))
        .collect::<Result<Vec<_>, _>>()?;
    validate_paths(&paths)?;
    let source_dir = directory.join("sources");
    let named_dir = directory.join("named-sources");
    tokio::fs::create_dir(directory)
        .await
        .map_err(|e| e.to_string())?;
    tokio::fs::create_dir(&source_dir)
        .await
        .map_err(|e| e.to_string())?;
    tokio::fs::create_dir(&named_dir)
        .await
        .map_err(|e| e.to_string())?;
    let mut inputs = Vec::new();
    for (source, paths) in sources.iter().zip(&paths) {
        let path = source_dir.join(
            paths
                .legacy
                .strip_prefix("/evidence/")
                .ok_or("invalid legacy path")?,
        );
        tokio::fs::write(&path, &source.bytes)
            .await
            .map_err(|e| e.to_string())?;
        readonly(&path).await?;
        let named = named_dir.join(
            paths
                .named
                .strip_prefix("/sources/")
                .ok_or("invalid named path")?,
        );
        tokio::fs::create_dir_all(named.parent().ok_or("missing source parent")?)
            .await
            .map_err(|e| e.to_string())?;
        // Both views contain the same immutable bytes, without another copy or a link
        // to the live workspace. The entire views are mounted read-only below.
        tokio::fs::hard_link(&path, &named)
            .await
            .map_err(|e| e.to_string())?;
        inputs.push(
            json!({"source_id":source.id,"source_subject_hash":source.subject,
            "path":paths.legacy,"named_path":paths.named,"captured_path":source.captured_path,
            "sha256":Digest::of(&source.bytes),"bytes":source.bytes.len()}),
        );
    }
    let manifest = named_dir.join("manifest.json");
    tokio::fs::write(
        &manifest,
        json!({"source_layout":SOURCE_LAYOUT,"inputs":inputs}).to_string(),
    )
    .await
    .map_err(|e| e.to_string())?;
    readonly(&manifest).await?;
    let program = directory.join("inspection.py");
    tokio::fs::write(&program, python)
        .await
        .map_err(|e| e.to_string())?;
    readonly(&program).await?;
    // Resolve a local image before executing; the inspected program has no public network.
    let pinned = crate::checks::pin_environment(
        env,
        remaining
            .saturating_sub(started.elapsed())
            .min(Duration::from_secs(10)),
    )
    .await
    .map_err(|e| e.to_string())?;
    let image = pinned.image;
    let name = crate::checks::unique_name();
    let binding = digest_of(
        "frozen_computation_v2",
        &(
            SOURCE_LAYOUT,
            &subject,
            requirements,
            &inputs,
            python,
            &image,
            &env.user,
            &env.inputs,
            grant,
        ),
    );
    let mut command = tokio::process::Command::new(&env.docker);
    let resources = grant.resources.clone().unwrap_or_default();
    let memory = resources.memory_bytes.to_string();
    let cpus = format!(
        "{}.{:03}",
        resources.cpu_millis / 1000,
        resources.cpu_millis % 1000
    );
    let pids = resources.pids.to_string();
    let tmpfs = format!(
        "/tmp:rw,nosuid,nodev,size={},mode=1777",
        resources.tmpfs_bytes
    );
    command
        .args([
            "run",
            "--rm",
            "--name",
            &name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            &pids,
            "--memory",
            &memory,
            "--cpus",
            &cpus,
            "--user",
            &env.user,
            "--tmpfs",
            &tmpfs,
            "-e",
            "HOME=/tmp",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-w",
            "/tmp",
            "-v",
        ])
        .arg(format!("{}:/evidence:ro", source_dir.display()))
        .arg("-v")
        .arg(format!("{}:/sources:ro", named_dir.display()))
        .arg("-v")
        .arg(format!("{}:/inspection.py:ro", program.display()))
        .args(env.input_mount_args()?)
        .args(["--entrypoint", "python3", &image, "-I", "/inspection.py"]);
    let available = remaining
        .saturating_sub(started.elapsed())
        .min(Duration::from_secs(grant.timeout_secs));
    if available.is_zero() {
        return Err("original worker deadline exhausted before computation".into());
    }
    let output = crate::execution::run(
        command,
        &directory.join("action"),
        binding,
        available,
        Some(crate::execution::Cleanup::new(
            env.docker.clone(),
            vec!["rm".into(), "-f".into(), name],
        )),
    )
    .await
    .map_err(|e| e.to_string())?;
    let out_cap = grant.max_output_bytes - output.stderr.len().min(grant.max_output_bytes / 2);
    let err_cap = grant.max_output_bytes - output.stdout.len().min(out_cap);
    let truncated = output.stdout.len() > out_cap || output.stderr.len() > err_cap;
    let stdout = crate::workers::bounded_head_tail(&output.stdout, out_cap);
    let stderr = crate::workers::bounded_head_tail(&output.stderr, err_cap);
    Ok(
        json!({"schema":3,"source_layout":SOURCE_LAYOUT,"subject_hash":subject,"requirements":requirements,"inputs":inputs,"task_inputs":env.input_capabilities(),
        "program_sha256":Digest::of(python.as_bytes()),"program":python,"image_id":image,"resources":resources,
        "execution":output.receipt,"stdout":stdout,"stderr":stderr,"output_truncated":truncated,
        "interpretation":"An observed computation, not an automatic theorem proof or permission to settle."}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_layout_preserves_names_and_separates_versions() {
        let current = source_paths("file:7", "data/archive.tar.gz").unwrap();
        let original = source_paths("baseline:7", "data/archive.tar.gz").unwrap();
        assert_eq!(current.legacy, "/evidence/file-7");
        assert_eq!(current.named, "/sources/current/data/archive.tar.gz");
        assert_eq!(original.named, "/sources/baseline/data/archive.tar.gz");
        validate_paths(&[current, original]).unwrap();
        for path in [
            "",
            "/absolute.py",
            "../escape.py",
            "a/../b.py",
            "a//b.py",
            "./file.py",
            "a/",
            "a\0b",
        ] {
            assert!(source_paths("file:0", path).is_err(), "{path:?}");
        }
        for id in ["file:01", "file:../x", "file:-1", "context:0"] {
            assert!(source_paths(id, "ok.py").is_err(), "{id}");
        }
    }

    #[test]
    fn source_layout_refuses_alias_duplicate_and_parent_collisions() {
        for pairs in [
            [("file:0", "a.py"), ("file:0", "b.py")],
            [("file:0", "a.py"), ("file:1", "a.py")],
            [("file:0", "pkg"), ("file:1", "pkg/module.py")],
            [("file:1", "pkg/module.py"), ("file:0", "pkg")],
        ] {
            let paths: Vec<_> = pairs
                .into_iter()
                .map(|(id, path)| source_paths(id, path).unwrap())
                .collect();
            assert!(validate_paths(&paths).is_err());
        }
    }
}
