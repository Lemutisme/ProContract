//! A host-provisioned observation source. The domain kernel still binds the
//! resulting immutable subject; this adapter neither judges nor schedules work.
use std::path::Path;
use std::time::Duration;

use crate::settings::CaptureProvider;
use serde::{Deserialize, Serialize};
use tokio::process::Command;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    schema: String,
    container_id: String,
    projection_root: String,
    scope: String,
    observation_sha256: String,
    /// Optional producer-declared namespaces; legacy providers remain readable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    projection: Option<super::capture_projection::Projection>,
}

pub(super) fn actions_root(root: &Path) -> std::path::PathBuf {
    root.with_extension("capture-actions")
}

pub(super) async fn materialize(
    provider: &CaptureProvider,
    root: &Path,
    timeout_secs: u64,
) -> Result<Option<super::capture_projection::Projection>, String> {
    if !provider.program.is_absolute() || provider.timeout_secs == 0 || timeout_secs == 0 {
        return Err("invalid capture provider program or declared timeout".into());
    }
    let parent = actions_root(root);
    tokio::fs::create_dir_all(&parent)
        .await
        .map_err(|e| e.to_string())?;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let directory = parent.join(format!("capture-{nonce}"));
    let mut command = Command::new(&provider.program);
    command.args(&provider.args);
    let output = crate::execution::run(
        command,
        &directory,
        crate::digest_of("capture_provider_action", &(provider, root)),
        Duration::from_secs(timeout_secs.min(provider.timeout_secs)),
        None,
    )
    .await
    .map_err(|e| e.to_string())?;
    if !output.receipt.completed() {
        return Err(format!(
            "capture provider failed: {:?}; {}",
            output.receipt.state, output.stderr
        ));
    }
    if output.stdout.len() > 65_536 {
        return Err("capture provider receipt exceeds allowance".into());
    }
    let receipt: Receipt = serde_json::from_str(&output.stdout).map_err(|e| e.to_string())?;
    if receipt.schema != "procontract_capture_provider/v1"
        || receipt.container_id != provider.container_id
        || Some(receipt.projection_root.as_str()) != root.to_str()
        || receipt.scope.is_empty()
        || receipt.observation_sha256.len() != 64
        || !receipt
            .observation_sha256
            .bytes()
            .all(|b| b.is_ascii_hexdigit())
    {
        return Err(
            "capture provider receipt does not bind the admitted source and projection".into(),
        );
    }
    let observed = std::fs::read(root.join("OBSERVATION.json")).map_err(|e| e.to_string())?;
    if codex_pro_contract::Digest::of(&observed).to_string() != receipt.observation_sha256 {
        return Err("capture provider observation digest mismatch".into());
    }
    if let Some(projection) = &receipt.projection {
        projection.validate()?;
    }
    let projection = receipt.projection.clone();
    let record = serde_json::json!({"provider": provider, "receipt": receipt,
        "meaning":"Host-provisioned scoped observations, not an attestation of complete execution state or correctness."});
    std::fs::write(
        root.join("CAPTURE_PROVIDER.json"),
        serde_json::to_vec(&record).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    Ok(projection)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(root: &Path, digest: &str) -> CaptureProvider {
        let receipt = serde_json::json!({
            "schema": "procontract_capture_provider/v1", "container_id": "a".repeat(64),
            "projection_root": root, "scope": "bounded observation; bytes may be omitted",
            "observation_sha256": digest,
        });
        CaptureProvider {
            program: "/bin/sh".into(),
            args: vec![
                "-c".into(),
                "printf '%s' \"$1\"".into(),
                "receipt".into(),
                receipt.to_string(),
            ],
            timeout_secs: 2,
            container_id: "a".repeat(64),
        }
    }

    #[tokio::test]
    async fn provider_receipt_binds_actual_observation_bytes_and_source() {
        let root = tempfile::tempdir().unwrap();
        let bytes = b"{\"scope\":\"bounded\"}";
        std::fs::write(root.path().join("OBSERVATION.json"), bytes).unwrap();
        let provider = fixture(
            root.path(),
            &codex_pro_contract::Digest::of(bytes).to_string(),
        );
        materialize(&provider, root.path(), provider.timeout_secs)
            .await
            .unwrap();
        let record: serde_json::Value = serde_json::from_slice(
            &std::fs::read(root.path().join("CAPTURE_PROVIDER.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(record["receipt"]["container_id"], provider.container_id);
        assert_eq!(record["provider"]["program"], "/bin/sh");
    }

    #[tokio::test]
    async fn changed_bytes_or_source_fail_before_capture() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("OBSERVATION.json"), b"different").unwrap();
        let mut provider = fixture(root.path(), &"0".repeat(64));
        assert!(
            materialize(&provider, root.path(), provider.timeout_secs)
                .await
                .unwrap_err()
                .contains("digest mismatch")
        );
        provider.container_id = "b".repeat(64);
        assert!(
            materialize(&provider, root.path(), provider.timeout_secs)
                .await
                .unwrap_err()
                .contains("admitted source")
        );
        assert!(!root.path().join("CAPTURE_PROVIDER.json").exists());
    }

    #[tokio::test]
    async fn failed_provider_is_not_an_empty_successful_observation() {
        let root = tempfile::tempdir().unwrap();
        let mut provider = fixture(root.path(), &"0".repeat(64));
        provider.args = vec!["-c".into(), "exit 7".into()];
        assert!(
            materialize(&provider, root.path(), provider.timeout_secs)
                .await
                .unwrap_err()
                .contains("provider failed")
        );
        assert!(!root.path().join("CAPTURE_PROVIDER.json").exists());
    }
    fn running(pid: u32) -> bool {
        std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .ok()
            .is_some_and(|s| {
                s.rsplit_once(')')
                    .is_some_and(|(_, tail)| !tail.trim_start().starts_with('Z'))
            })
    }

    #[tokio::test]
    async fn timeout_kills_provider_grandchild_before_return() {
        let root = tempfile::tempdir().unwrap();
        let pidfile = root.path().join("grandchild.pid");
        let mut provider = fixture(root.path(), &"0".repeat(64));
        provider.timeout_secs = 1;
        provider.args = vec![
            "-c".into(),
            "(while :; do sleep 1; done) & echo $! > \"$1\"; wait".replace("\\\"", "\""),
            "fixture".into(),
            pidfile.to_string_lossy().into_owned(),
        ];
        assert!(
            materialize(&provider, root.path(), 1)
                .await
                .unwrap_err()
                .contains("TimedOut")
        );
        let pid: u32 = std::fs::read_to_string(pidfile)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(!running(pid), "provider grandchild survived timeout");
        assert!(!crate::execution::has_owned_under(&actions_root(
            root.path()
        )));
    }

    #[tokio::test]
    async fn cancelled_provider_finalizes_owned_descendants() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().to_path_buf();
        let pidfile = path.join("grandchild.pid");
        let mut provider = fixture(&path, &"0".repeat(64));
        provider.timeout_secs = 120;
        provider.args = vec![
            "-c".into(),
            "(while :; do sleep 1; done) & echo $! > \"$1\"; wait".replace("\\\"", "\""),
            "fixture".into(),
            pidfile.to_string_lossy().into_owned(),
        ];
        let task = tokio::spawn(async move { materialize(&provider, &path, 120).await });
        for _ in 0..100 {
            if pidfile.exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let pid: u32 = std::fs::read_to_string(pidfile)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        task.abort();
        let _ = task.await;
        crate::execution::wait_for_owned_under(&actions_root(root.path())).await;
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(!running(pid), "provider grandchild survived cancellation");
        assert!(!crate::execution::has_owned_under(&actions_root(
            root.path()
        )));
    }
}
