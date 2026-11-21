//! A provisioner-owned container freeze, separate from artifact hashing.
//! The caller reserves a durable publication before acquiring this barrier.

use std::time::Duration;
use tokio::process::Command;

use crate::EvaluationProfile;

pub(crate) struct CaptureBarrier {
    docker: String,
    ids: Vec<String>,
    provider_actions: Option<std::path::PathBuf>,
}

async fn docker(program: &str, args: &[&str]) -> Result<String, String> {
    let output = tokio::time::timeout(
        Duration::from_secs(10),
        Command::new(program).args(args).kill_on_drop(true).output(),
    )
    .await
    .map_err(|_| "capture barrier command timed out")?
    .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(format!(
            "capture barrier command failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    String::from_utf8(output.stdout).map_err(|e| e.to_string())
}

impl CaptureBarrier {
    pub(crate) async fn acquire(profile: &EvaluationProfile, label: &str) -> Result<Self, String> {
        if !label.starts_with("procontract.task=")
            || label.len() > 128
            || !label
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b".=_-".contains(&b))
        {
            return Err("invalid provisioner capture label".into());
        }
        let ids: Vec<String> = docker(
            &profile.check.docker,
            &[
                "ps",
                "--no-trunc",
                "--filter",
                &format!("label={label}"),
                "--format",
                "{{.ID}}",
            ],
        )
        .await?
        .lines()
        .map(str::to_string)
        .collect();
        // The initial adapter requires one exclusively owned workspace executor.
        // It never guesses which of several live writers is safe to freeze.
        if ids.len() != 1
            || ids
                .iter()
                .any(|s| s.len() != 64 || !s.bytes().all(|b| b.is_ascii_hexdigit()))
        {
            return Err("capture requires exactly one provisioned workspace executor".into());
        }
        let metadata: serde_json::Value =
            serde_json::from_str(&docker(&profile.check.docker, &["inspect", &ids[0]]).await?)
                .map_err(|e| e.to_string())?;
        let container = &metadata[0];
        let mounted = container["Mounts"].as_array().is_some_and(|mounts| {
            mounts.iter().any(|mount| {
                mount["Source"].as_str() == profile.workspace_host_root.to_str()
                    && mount["Destination"] == profile.workspace_container_root
                    && mount["RW"] == true
            })
        });
        let source_matches = match &profile.capture_provider {
            Some(provider) => provider.container_id == ids[0],
            None => mounted,
        };
        if !source_matches
            || container["State"]["Running"] != true
            || container["State"]["Paused"] != false
        {
            return Err(
                "capture executor identity, mount or lifecycle does not match the grant".into(),
            );
        }
        let barrier = Self {
            docker: profile.check.docker.clone(),
            ids,
            provider_actions: profile
                .capture_provider
                .as_ref()
                .map(|_| super::capture_provider::actions_root(&profile.workspace_host_root)),
        };
        // Arm cleanup before dispatch: cancellation cannot leave a known container frozen.
        docker(&barrier.docker, &["pause", &barrier.ids[0]]).await?;
        let paused = docker(
            &barrier.docker,
            &["inspect", "--format", "{{.State.Paused}}", &barrier.ids[0]],
        )
        .await?;
        if paused.trim() != "true" {
            return Err("executor freeze was not confirmed".into());
        }
        Ok(barrier)
    }

    /// Capture a projection under a real source freeze. An outer progress owner
    /// may already hold the freeze; in that case this scope never resumes it.
    pub(crate) async fn projection(profile: &EvaluationProfile) -> Result<Self, String> {
        let provider = profile
            .capture_provider
            .as_ref()
            .ok_or("capture provider absent")?;
        let id = &provider.container_id;
        if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("invalid capture provider source identity".into());
        }
        let metadata: serde_json::Value =
            serde_json::from_str(&docker(&profile.check.docker, &["inspect", id]).await?)
                .map_err(|e| e.to_string())?;
        if metadata[0]["Id"] != *id || metadata[0]["State"]["Running"] != true {
            return Err("capture provider source is not the provisioned running container".into());
        }
        if metadata[0]["State"]["Paused"] == true {
            return Ok(Self {
                docker: profile.check.docker.clone(),
                ids: Vec::new(),
                provider_actions: None,
            });
        }
        let barrier = Self {
            docker: profile.check.docker.clone(),
            ids: vec![id.clone()],
            provider_actions: Some(super::capture_provider::actions_root(
                &profile.workspace_host_root,
            )),
        };
        docker(&barrier.docker, &["pause", id]).await?;
        if docker(
            &barrier.docker,
            &["inspect", "--format", "{{.State.Paused}}", id],
        )
        .await?
        .trim()
            != "true"
        {
            return Err("capture provider source freeze was not confirmed".into());
        }
        Ok(barrier)
    }

    pub(crate) fn identities(&self) -> &[String] {
        &self.ids
    }

    pub(crate) async fn resume(mut self) -> Result<(), String> {
        if let Some(directory) = &self.provider_actions {
            crate::execution::wait_for_owned_under(directory).await;
            if crate::execution::has_owned_under(directory) {
                return Err("capture provider effects remain owned; source remains paused".into());
            }
        }
        for id in &self.ids {
            docker(&self.docker, &["unpause", id]).await?;
            if docker(
                &self.docker,
                &["inspect", "--format", "{{.State.Paused}}", id],
            )
            .await?
            .trim()
                != "false"
            {
                return Err("executor unfreeze was not confirmed".into());
            }
        }
        self.ids.clear();
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn test_only() -> Self {
        Self {
            docker: String::new(),
            ids: Vec::new(),
            provider_actions: None,
        }
    }
}

impl Drop for CaptureBarrier {
    fn drop(&mut self) {
        if !self.ids.is_empty()
            && let Ok(runtime) = tokio::runtime::Handle::try_current()
        {
            let program = self.docker.clone();
            let ids = std::mem::take(&mut self.ids);
            let provider_actions = self.provider_actions.clone();
            runtime.spawn(async move {
                if let Some(directory) = provider_actions {
                    crate::execution::wait_for_owned_under(&directory).await;
                    if crate::execution::has_owned_under(&directory) {
                        tracing::warn!(
                            "capture provider effects unresolved; retaining source pause"
                        );
                        return;
                    }
                }
                for id in ids {
                    let _ = docker(&program, &["unpause", &id]).await;
                }
            });
        }
    }
}

#[cfg(test)]
mod provider_lifecycle_tests {
    use super::*;

    // Runs only inside an explicitly admitted fixture container, never default cargo test.
    #[tokio::test]
    #[ignore = "requires PROCONTRACT_CAPTURE_TEST_PROFILE with an owned running container"]
    async fn real_source_pause_is_retained_until_provider_effects_close() {
        let path = std::env::var("PROCONTRACT_CAPTURE_TEST_PROFILE").unwrap();
        let mut profile: EvaluationProfile =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let id = profile
            .capture_provider
            .as_ref()
            .unwrap()
            .container_id
            .clone();
        let root = profile.workspace_host_root.clone();
        std::fs::create_dir_all(&root).unwrap();
        let provider = profile.capture_provider.as_mut().unwrap();
        provider.program = "/bin/sh".into();
        provider.args = vec!["-c".into(), "exit 7".into()];
        provider.timeout_secs = 120; // no unrelated 60-second provider ceiling
        let outer = CaptureBarrier::projection(&profile).await.unwrap();
        assert_eq!(
            docker("docker", &["inspect", "--format", "{{.State.Paused}}", &id])
                .await
                .unwrap()
                .trim(),
            "true"
        );
        let inner = CaptureBarrier::projection(&profile).await.unwrap();
        assert!(inner.identities().is_empty());
        assert!(
            super::super::capture_provider::materialize(
                profile.capture_provider.as_ref().unwrap(),
                &root,
                120
            )
            .await
            .is_err()
        );
        inner.resume().await.unwrap();
        assert_eq!(
            docker("docker", &["inspect", "--format", "{{.State.Paused}}", &id])
                .await
                .unwrap()
                .trim(),
            "true",
            "borrowed freeze was incorrectly returned"
        );
        outer.resume().await.unwrap();
        assert_eq!(
            docker("docker", &["inspect", "--format", "{{.State.Paused}}", &id])
                .await
                .unwrap()
                .trim(),
            "false"
        );

        let pidfile = root.join("grandchild.pid");
        profile.capture_provider.as_mut().unwrap().args = vec![
            "-c".into(),
            r#"(while :; do sleep 1; done) & echo $! > "$1"; wait"#.into(),
            "fixture".into(),
            pidfile.to_string_lossy().into_owned(),
        ];
        let future_root = root.clone();
        let task = tokio::spawn(async move {
            let barrier = CaptureBarrier::projection(&profile).await.unwrap();
            let _ = super::super::capture_provider::materialize(
                profile.capture_provider.as_ref().unwrap(),
                &future_root,
                120,
            )
            .await;
            barrier.resume().await.unwrap();
        });
        for _ in 0..200 {
            if pidfile.exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let pid: u32 = std::fs::read_to_string(&pidfile)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        task.abort();
        let _ = task.await;
        for _ in 0..100 {
            if docker("docker", &["inspect", "--format", "{{.State.Paused}}", &id])
                .await
                .unwrap()
                .trim()
                == "false"
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert_eq!(
            docker("docker", &["inspect", "--format", "{{.State.Paused}}", &id])
                .await
                .unwrap()
                .trim(),
            "false"
        );
        let running = std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .ok()
            .is_some_and(|s| {
                s.rsplit_once(')')
                    .is_some_and(|(_, tail)| !tail.trim_start().starts_with('Z'))
            });
        assert!(
            !running,
            "provider grandchild remained active after source unpause"
        );
        assert!(!crate::execution::has_owned_under(
            &super::super::capture_provider::actions_root(&root)
        ));
    }
}

#[cfg(test)]
mod provider_identity_tests {
    use super::*;

    #[tokio::test]
    #[ignore = "requires an admitted owned source and actual projection provider"]
    async fn unchanged_paused_source_has_stable_native_subject() {
        let path = std::env::var("PROCONTRACT_CAPTURE_TEST_PROFILE").unwrap();
        let profile: EvaluationProfile =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let root = &profile.workspace_host_root;
        let store_dir = tempfile::tempdir().unwrap();
        let store = crate::BlobStore::open(store_dir.path()).unwrap();
        let policy = crate::CapturePolicy::standard(Vec::new());
        let barrier = CaptureBarrier::projection(&profile).await.unwrap();
        let provider = profile.capture_provider.as_ref().unwrap();
        super::super::capture_provider::materialize(provider, root, provider.timeout_secs)
            .await
            .unwrap();
        let first = crate::capture(root, &policy, &store).unwrap();
        tokio::time::sleep(Duration::from_millis(100)).await;
        super::super::capture_provider::materialize(provider, root, provider.timeout_secs)
            .await
            .unwrap();
        let second = crate::capture(root, &policy, &store).unwrap();
        assert_eq!(
            first.subject_hash, second.subject_hash,
            "capture metadata manufactured a new subject without a source-state change"
        );
        println!("stable_subject_hash={}", first.subject_hash);
        barrier.resume().await.unwrap();
    }
}
