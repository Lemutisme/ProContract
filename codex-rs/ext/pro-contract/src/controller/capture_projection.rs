//! Provenance for a host-produced projection, separate from kernel subject identity.
//! A task-owned file cannot declare itself a host observation. Only the configured
//! capture provider supplies this description, bound after immutable capture.

use std::collections::BTreeSet;

use codex_pro_contract::Digest;
use serde::Deserialize;
use serde::Serialize;

use crate::Entry;
use crate::EntryKind;
use crate::Subject;

const RECORD_KIND: &str = "capture_projection_v1";
pub(super) const PROVIDER_RECORD: &str = "CAPTURE_PROVIDER.json";

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FileProjection {
    /// Relative prefix in the captured package; never inferred from filenames.
    pub prefix: String,
    /// Absolute path in the observed execution environment.
    pub task_root: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ObservationKind {
    ArtifactObservation,
    ExecutionObservation,
    CaptureMetadata,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ObservationSource {
    pub path: String,
    pub sha256: Digest,
    pub kind: ObservationKind,
    /// What was actually observed, including omissions and historical scope.
    pub scope: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Projection {
    pub task_files: FileProjection,
    pub observations: Vec<ObservationSource>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct BoundProjection {
    pub subject_hash: Digest,
    pub producer: Digest,
    pub projection: Projection,
}

fn relative_path(path: &str) -> bool {
    !path.is_empty()
        && !path.contains('\0')
        && path
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
}

impl Projection {
    pub(super) fn validate(&self) -> Result<(), String> {
        let root = &self.task_files.task_root;
        if !relative_path(&self.task_files.prefix)
            || !(root == "/" || root.starts_with('/') && relative_path(&root[1..]))
        {
            return Err("invalid host projection namespace".into());
        }
        let mut paths = BTreeSet::new();
        for source in &self.observations {
            if !relative_path(&source.path)
                || !paths.insert(&source.path)
                || source.scope.is_empty()
                || source.scope.len() > 4096
                || self.task_path(&source.path).is_some()
            {
                return Err("invalid or overlapping host observation source".into());
            }
        }
        Ok(())
    }

    pub(crate) fn task_path(&self, captured: &str) -> Option<String> {
        let suffix = captured
            .strip_prefix(&self.task_files.prefix)?
            .strip_prefix('/')?;
        if !relative_path(suffix) {
            return None;
        }
        Some(format!(
            "{}/{}",
            self.task_files.task_root.trim_end_matches('/'),
            suffix
        ))
    }

    pub(crate) fn observation(&self, path: &str) -> Option<&ObservationSource> {
        self.observations.iter().find(|source| source.path == path)
    }
}

impl BoundProjection {
    pub(crate) fn validate(&self, subject: &Subject) -> Result<(), String> {
        self.projection.validate()?;
        if self.subject_hash != subject.subject_hash {
            return Err("observation provenance belongs to another captured subject".into());
        }
        for source in &self.projection.observations {
            let entry = subject
                .manifest
                .entries
                .iter()
                .find(|entry| entry.path == source.path);
            if !matches!(entry.map(|entry| &entry.kind), Some(EntryKind::File { digest, .. }) if *digest == source.sha256)
            {
                return Err("host observation differs from its frozen source bytes".into());
            }
        }
        Ok(())
    }
}

pub(super) async fn bind(
    ledger: &crate::Ledger,
    subject: &Subject,
    producer: Digest,
    mut projection: Projection,
) -> Result<(), String> {
    projection.validate()?;
    if projection.observation(PROVIDER_RECORD).is_some() {
        return Err("provider cannot replace the native capture receipt origin".into());
    }
    let entry = subject
        .manifest
        .entries
        .iter()
        .find(|entry| entry.path == PROVIDER_RECORD);
    let Some(EntryKind::File { digest, .. }) = entry.map(|entry| &entry.kind) else {
        return Err("native capture receipt was not retained in the subject".into());
    };
    projection.observations.push(ObservationSource {
        path: PROVIDER_RECORD.into(),
        sha256: *digest,
        kind: ObservationKind::CaptureMetadata,
        scope: "Native capture-provider receipt; not an executor-edited task file or proof of task correctness.".into(),
    });
    let bound = BoundProjection {
        subject_hash: subject.subject_hash,
        producer,
        projection,
    };
    bound.validate(subject)?;
    let key = subject.subject_hash.to_string();
    if !ledger
        .compare_exchange_record(RECORD_KIND, &key, None, &bound)
        .await
        .map_err(|e| e.to_string())?
    {
        let prior: Option<BoundProjection> = ledger
            .record(RECORD_KIND, &key)
            .await
            .map_err(|e| e.to_string())?;
        if prior.as_ref() != Some(&bound) {
            return Err("captured observation provenance cannot be reinterpreted".into());
        }
    }
    Ok(())
}

pub(super) async fn load(
    ledger: &crate::Ledger,
    subject: &Subject,
) -> Result<Option<BoundProjection>, String> {
    let bound: Option<BoundProjection> = ledger
        .record(RECORD_KIND, &subject.subject_hash.to_string())
        .await
        .map_err(|e| e.to_string())?;
    if let Some(bound) = &bound {
        bound.validate(subject)?;
    }
    Ok(bound)
}

/// Only copied task entries take part in this view. Uncopied binary/metadata
/// observations remain separately readable; absence here is not task deletion.
pub(super) fn task_entry_path(
    entry: &Entry,
    projection: Option<&BoundProjection>,
) -> Option<String> {
    match projection {
        Some(bound) => bound.projection.task_path(&entry.path),
        None => Some(entry.path.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn projection() -> Projection {
        Projection {
            task_files: FileProjection {
                prefix: "files".into(),
                task_root: "/".into(),
            },
            observations: vec![ObservationSource {
                path: "observed.json".into(),
                sha256: Digest::of(b"observed"),
                kind: ObservationKind::ExecutionObservation,
                scope: "Past call/result pairs; omitted calls remain unknown.".into(),
            }],
        }
    }

    #[test]
    fn source_namespace_does_not_exempt_a_task_file_with_the_same_basename() {
        let projection = projection();
        projection.validate().unwrap();
        assert_eq!(
            projection.task_path("files/app/observed.json"),
            Some("/app/observed.json".into())
        );
        assert!(projection.observation("files/app/observed.json").is_none());
        assert!(projection.task_path("observed.json").is_none());
        assert!(projection.task_path("files-other/app/x").is_none());
        // These are ordinary POSIX filenames, not separators or drive prefixes.
        for name in ["a:b", "back\\slash", "line\nbreak"] {
            assert_eq!(
                projection.task_path(&format!("files/app/{name}")),
                Some(format!("/app/{name}"))
            );
        }
    }

    #[test]
    fn traversal_and_source_overlap_are_not_provenance() {
        for prefix in ["../files", "/files", "files/", "files//x", "files\0x"] {
            let mut projection = projection();
            projection.task_files.prefix = prefix.into();
            assert!(projection.validate().is_err(), "{prefix}");
        }
        let mut projection = projection();
        projection.observations[0].path = "files/app/observed.json".into();
        assert!(projection.validate().is_err());
    }

    #[tokio::test]
    async fn immutable_projection_keeps_observer_bytes_without_reporting_them_as_task_edits() {
        let root = tempfile::tempdir().unwrap();
        let workspace = root.path().join("workspace");
        std::fs::create_dir_all(workspace.join("files/app")).unwrap();
        std::fs::write(workspace.join("files/app/observed.json"), "task file").unwrap();
        std::fs::write(workspace.join(PROVIDER_RECORD), "native provider receipt").unwrap();
        let config = codex_state::SqliteConfig::new_for_testing(
            codex_utils_absolute_path::AbsolutePathBuf::from_absolute_path(root.path()).unwrap(),
        );
        let ledger = crate::Ledger::open(&config, root.path()).await.unwrap();
        let store = crate::BlobStore::open(&root.path().join("blobs")).unwrap();
        let policy = crate::CapturePolicy {
            version: 1,
            excluded_paths: vec![],
            max_file_bytes: 1 << 20,
            max_total_bytes: 1 << 24,
        };
        let mut subjects = Vec::new();
        for observation in [
            "binary digest and permissions observed; body omitted",
            "new execution observation; same task bytes",
        ] {
            std::fs::write(workspace.join("observed.json"), observation).unwrap();
            let subject = crate::capture(&workspace, &policy, &store).unwrap();
            let mut description = projection();
            description.observations[0].sha256 = Digest::of(observation.as_bytes());
            bind(
                &ledger,
                &subject,
                Digest::of(b"provider"),
                description.clone(),
            )
            .await
            .unwrap();
            // Recalling an identical capture neither reinterprets nor spends work.
            bind(&ledger, &subject, Digest::of(b"provider"), description)
                .await
                .unwrap();
            let origin = load(&ledger, &subject).await.unwrap().unwrap();
            let tasks: Vec<_> = subject
                .manifest
                .entries
                .iter()
                .filter_map(|entry| {
                    task_entry_path(entry, Some(&origin)).map(|path| (path, entry.kind.clone()))
                })
                .collect();
            assert_eq!(tasks.len(), 1);
            assert_eq!(tasks[0].0, "/app/observed.json");
            assert_eq!(
                store
                    .get(&origin.projection.observations[0].sha256)
                    .unwrap(),
                observation.as_bytes()
            );
            subjects.push((subject, tasks, origin));
        }
        assert_ne!(subjects[0].0.subject_hash, subjects[1].0.subject_hash);
        assert_eq!(subjects[0].1, subjects[1].1);
        assert!(subjects[0].2.validate(&subjects[1].0).is_err());
        let mut conflicting = projection();
        conflicting.observations[0].sha256 =
            Digest::of(b"new execution observation; same task bytes");
        assert!(
            bind(
                &ledger,
                &subjects[1].0,
                Digest::of(b"different producer"),
                conflicting
            )
            .await
            .is_err()
        );
    }
}
