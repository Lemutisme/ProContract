//! Actual restore() over exported native source prefixes. No paid or container ports.
use super::*;
use crate::controller::ports::{SuccessorExit, SuccessorTurn};
use crate::{CapturePolicy, ObservationBatch, Subject};
use pretty_assertions::assert_eq;
use serde_json::{Value, json};
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Default)]
struct NoEffectsPorts(AtomicUsize);
impl NoEffectsPorts {
    fn deny(&self) -> String {
        self.0.fetch_add(1, Ordering::SeqCst);
        "external effect is forbidden by native prefix control".into()
    }
}
impl Ports for NoEffectsPorts {
    fn interrupt_executor(&self) -> PortFuture<'_, Result<(), String>> {
        let reason = self.deny();
        Box::pin(async move { Err(reason) })
    }
    fn capture_barrier<'a>(
        &'a self,
        _: &'a EvaluationProfile,
        _: &'a str,
    ) -> PortFuture<'a, Result<crate::controller::capture_barrier::CaptureBarrier, String>> {
        let reason = self.deny();
        Box::pin(async move { Err(reason) })
    }
    fn run_successor(&self, _: SuccessorTurn) -> PortFuture<'_, Result<SuccessorExit, String>> {
        let reason = self.deny();
        Box::pin(async move { Err(reason) })
    }
    fn run_worker(&self, _: WorkerTurn) -> PortFuture<'_, Result<String, WorkerError>> {
        let reason = self.deny();
        Box::pin(async move { Err(WorkerError::Start(reason)) })
    }
    fn observe<'a>(
        &'a self,
        _: &'a CheckEnvironment,
        _: &'a Path,
        _: &'a str,
        _: &'a [DifferentialCase],
    ) -> PortFuture<'a, Result<ObservationBatch, CheckError>> {
        let reason = self.deny();
        Box::pin(async move { Err(CheckError::Launch(reason)) })
    }
    fn run_checks<'a>(
        &'a self,
        _: &'a CheckEnvironment,
        _: &'a Path,
        _: &'a Path,
        _: &'a EvidencePolicy,
    ) -> PortFuture<'a, Result<CheckReceipts, CheckError>> {
        let reason = self.deny();
        Box::pin(async move { Err(CheckError::Launch(reason)) })
    }
    fn worker_identity(&self) -> WorkerIdentity {
        WorkerIdentity {
            model: None,
            effort: None,
        }
    }
    fn submit_repair(&self, _: String, _: String) -> PortFuture<'_, Result<(), String>> {
        let reason = self.deny();
        Box::pin(async move { Err(reason) })
    }
}

fn verify_materialized(root: &Path, subject: &Subject) {
    let temporary = tempfile::tempdir().unwrap();
    let blobs = BlobStore::open(temporary.path()).unwrap();
    let recaptured = crate::capture(root, &CapturePolicy::standard(Vec::new()), &blobs).unwrap();
    assert_eq!(recaptured.subject_hash, subject.subject_hash);
}

#[tokio::test]
async fn actual_native_restore_of_two_exported_prefixes_has_no_effects() {
    let pair_path = std::path::PathBuf::from(
        std::env::var_os("NATIVE_PREFIX_PAIR")
            .expect("provide an explicitly exported scratch NATIVE_PREFIX_PAIR"),
    );
    let pair: Value = serde_json::from_slice(&std::fs::read(&pair_path).unwrap()).unwrap();
    assert_eq!(pair["schema"], "native-prefix-pair/v1");
    let metadata = &pair["metadata"];
    let expected: codex_pro_contract::Contract =
        serde_json::from_value(metadata["contract"].clone()).unwrap();
    let thread: ThreadId = serde_json::from_value(metadata["thread_id"].clone()).unwrap();
    let mut outcomes = Vec::new();
    for side in pair["stores"].as_array().unwrap() {
        let dir = std::path::PathBuf::from(side["store"].as_str().unwrap());
        let settings: Settings =
            serde_json::from_slice(&std::fs::read(side["settings"].as_str().unwrap()).unwrap())
                .unwrap();
        let profile = settings.evaluation.clone().unwrap();
        let sqlite =
            SqliteConfig::new_for_testing(AbsolutePathBuf::from_absolute_path(&dir).unwrap());
        let ledger = Ledger::open(&sqlite, &dir).await.unwrap();
        let blobs = BlobStore::open(&dir.join("blobs")).unwrap();
        let mut materialized = Vec::new();
        for (name, field) in [("base", "base_subject"), ("candidate", "current_subject")] {
            let hash: Digest = serde_json::from_value(metadata[field].clone()).unwrap();
            let binding = ledger.subject_binding(&hash).await.unwrap().unwrap();
            let subject =
                codex_pro_contract_store::load_subject(&blobs, &binding.manifest, &hash).unwrap();
            let dest = dir.parent().unwrap().join("materialized").join(name);
            std::fs::create_dir_all(&dest).unwrap();
            codex_pro_contract_store::materialize(&subject, &blobs, &dest).unwrap();
            verify_materialized(&dest, &subject);
            materialized
                .push(json!({"name":name,"subject":hash,"path":dest,"manifest":binding.manifest}));
        }
        let before_events = ledger.events().await.unwrap();
        let before_journal = ledger.experiments().await.unwrap();
        let succession: Value = ledger
            .record("succession", &thread.to_string())
            .await
            .unwrap()
            .unwrap();
        let progress: Value = ledger
            .record("progress_grant_v1", &thread.to_string())
            .await
            .unwrap()
            .unwrap();
        let ports = Arc::new(NoEffectsPorts::default());
        let runtime = Arc::new(ThreadRuntime::new(
            thread,
            settings.clone(),
            profile,
            crate::workers::policies::Policies::default(),
            Stores {
                dir: dir.clone(),
                ledger: ledger.clone(),
                store: blobs.clone(),
            },
            ports.clone(),
        ));
        // This is the production recovery entry; it internally executes restore_state.
        runtime.restore().await;
        let status: Option<super::super::StatusRecord> = ledger
            .record(STATUS_KIND, &thread.to_string())
            .await
            .unwrap();
        let state = runtime.state.lock().await;
        let phase = format!("{:?}", state.phase);
        let active = state.contract.as_ref();
        let row = json!({"side":side["side"],"phase":phase,"status":status,"materialized":materialized,
            "contract":active.map(|a| &a.contract),"terms_hash":active.map(|a| crate::digest_of("terms", &a.terms)),
            "base_subject":active.map(|a| a.base.subject),"frozen_present":state.frozen.is_some(),
            "last_idle_present":state.last_idle.is_some(),"proposed_turns":state.proposed_turns.len(),
            "delivered_turns":state.delivered_turns.len(),"epoch":state.epoch,"repairs_used":state.repairs_used,
            "effect_port_calls":ports.0.load(Ordering::SeqCst),"old_grant_unchanged":true,
            "new_grant":false,"paid_calls":0,"container_calls":0});
        drop(state);
        std::fs::write(
            dir.parent().unwrap().join("RESTORE_READBACK.json"),
            serde_json::to_vec_pretty(&row).unwrap(),
        )
        .unwrap();
        // Preserve the first actual blocker before making this test fail.
        assert_eq!(
            runtime.state.lock().await.phase,
            super::super::Phase::Resting,
            "{row}"
        );
        assert_eq!(
            ledger.contract(&expected.id).await.unwrap().unwrap(),
            expected
        );
        assert_eq!(
            row["terms_hash"],
            serde_json::to_value(expected.bindings.terms_hash).unwrap()
        );
        assert_eq!(ledger.events().await.unwrap(), before_events);
        assert_eq!(ledger.experiments().await.unwrap(), before_journal);
        assert_eq!(
            ledger
                .record::<Value>("succession", &thread.to_string())
                .await
                .unwrap()
                .unwrap(),
            succession
        );
        assert_eq!(
            ledger
                .record::<Value>("progress_grant_v1", &thread.to_string())
                .await
                .unwrap()
                .unwrap(),
            progress
        );
        assert_eq!(ports.0.load(Ordering::SeqCst), 0);
        assert_eq!(
            before_events.len(),
            metadata["kernel_count"].as_u64().unwrap() as usize
        );
        assert_eq!(
            before_journal.len(),
            metadata["journal_count"].as_u64().unwrap() as usize
        );
        outcomes.push(row);
    }
    assert_eq!(outcomes.len(), 2);
    assert_eq!(outcomes[0]["contract"], outcomes[1]["contract"]);
    std::fs::write(pair_path.parent().unwrap().join("NATIVE_RESTORE_READBACK.json"),
        serde_json::to_vec_pretty(&json!({"actual_native_restore":true,"sides":outcomes,
            "source_prefix_unchanged":true,"scope":"institution and materialized subjects only; no handoff callback, environment or new grant"})).unwrap()).unwrap();
}
