use super::*;
use pretty_assertions::assert_eq;
use serde_json::json;

fn choose(ports: &FakePorts, action: &str, requirements: serde_json::Value) {
    let choice = if action == "rest" {
        json!({"action":"rest","reason":"No useful work selected."})
    } else {
        json!({"action":action,"requirements":requirements,"next_step":"Inspect the retained output with the available tools."})
    };
    ports
        .continuation_choices
        .lock()
        .unwrap()
        .push_back(json!({"choice":choice}).to_string());
}

fn observe(ports: &FakePorts, action: &str) {
    let choice = if action == "diagnose" {
        json!({"action":"diagnose","question":"Does the retained artifact satisfy R1?"})
    } else {
        json!({"action":action,"reason":"Reassess the same retained work using another observation."})
    };
    ports
        .continuation_choices
        .lock()
        .unwrap()
        .push_back(json!({"choice":choice}).to_string());
}

#[tokio::test]
async fn late_submission_outcomes_cannot_replace_a_new_owner_turn_status() {
    for error in [None, Some("late submission rejection".to_string())] {
        let gate = Arc::new(Notify::new());
        let mut ports = FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[CANNOT_JUDGE_REVIEW]);
        ports.repair_gate = Some(gate.clone());
        ports.repair_error = error;
        choose(&ports, "resume", json!(["R1"]));
        let lane = lane(ports).await;
        lane.runtime.intake(INTAKE.into()).await;
        lane.wait_for("working").await;
        lane.executor_turn("first", "done").await;
        tokio::time::timeout(Duration::from_secs(5), async {
            while lane.ports.repairs.lock().unwrap().is_empty() {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .unwrap();
        lane.runtime.turn_started().await;
        lane.runtime
            .record_status("working", "new owner turn", false)
            .await;
        let expected = lane.status().await;
        gate.notify_one();
        let mut tasks = std::mem::take(&mut *lane.runtime.tasks.lock().unwrap());
        while let Some(result) = tasks.join_next().await {
            result.unwrap();
        }
        assert_eq!(
            lane.status().await,
            expected,
            "old acknowledgement is historical, not the current disposition"
        );
        assert_eq!(
            lane.runtime.state.lock().await.phase,
            super::super::Phase::Working
        );
        let journal = lane.ledger.experiments().await.unwrap();
        assert_eq!(
            journal
                .iter()
                .filter(|r| r.event.body["phase"] == "continuation_submission")
                .count(),
            1
        );
        assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
        assert_eq!(
            lane.status().await.unwrap().repairs_used,
            1,
            "a superseded result does not refund the original admission"
        );
    }
}

#[tokio::test]
async fn selected_verification_after_defeat_uses_current_responsibility() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Fail, StepOutcome::Pass])
        .reviews(&[SUPPORT_REVIEW]);
    observe(&ports, "verify");
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    lane.wait_for("supported").await;
    let commands: Vec<_> = lane
        .ledger
        .events()
        .await
        .unwrap()
        .into_iter()
        .map(|e| e.command.command.kind())
        .collect();
    use codex_pro_contract::CommandKind::*;
    assert_eq!(commands, vec![Issue, Propose, Defeat, Propose, Support]);
    let prompts = lane.ports.continuation_prompts.lock().unwrap();
    let facts: serde_json::Value =
        serde_json::from_str(prompts[0].rsplit_once("\n\n").unwrap().1).unwrap();
    assert_eq!(facts["responsibility"]["version"], 3);
    assert_eq!(facts["responsibility"]["generation"], 2);
    assert!(facts["responsibility"]["candidate"].is_null());
    assert_eq!(facts["assessment"]["generation"], 1);
    let public: serde_json::Value = serde_json::from_slice(
        &lane
            .runtime
            .store
            .get(&serde_json::from_value(facts["public_evidence"].clone()).unwrap())
            .unwrap(),
    )
    .unwrap();
    assert_eq!(
        public["generation"], 1,
        "advancing responsibility must not rewrite historical evidence"
    );
}

#[tokio::test]
async fn local_observations_after_defeat_preserve_work_for_selected_verification() {
    for interrupted in [false, true] {
        for action in ["check", "diagnose"] {
            let ports = FakePorts::new(CONTRACT_DRAFT)
                .checks(&[StepOutcome::Fail, StepOutcome::Pass, StepOutcome::Pass])
                .reviews(&[SUPPORT_REVIEW, SUPPORT_REVIEW]);
            if interrupted {
                observe(&ports, "verify");
            }
            observe(&ports, action);
            observe(&ports, "verify");
            let lane = lane(ports).await;
            lane.runtime.intake(INTAKE.into()).await;
            lane.wait_for("working").await;
            if interrupted {
                lane.runtime.turn_started().await;
                lane.write_answer("done");
                lane.runtime.turn_stopped("interrupted".into()).await;
                lane.runtime.thread_idle(ThreadIdleCause::Failed).await;
            } else {
                lane.executor_turn("delivery", "done").await;
            }
            lane.wait_for("supported").await;
            let events = lane.ledger.events().await.unwrap();
            let commands: Vec<_> = events.iter().map(|e| e.command.command.kind()).collect();
            use codex_pro_contract::CommandKind::*;
            assert_eq!(
                commands,
                vec![Issue, Propose, Defeat, Propose, Support],
                "{interrupted}/{action}"
            );
            let subject = lane
                .contract()
                .await
                .unwrap()
                .candidate
                .unwrap()
                .subject_hash;
            let journal = lane.ledger.experiments().await.unwrap();
            let observations: Vec<_> = journal
                .iter()
                .filter(|r| r.event.body["phase"] == "work_assessed")
                .collect();
            assert_eq!(observations.len(), 1);
            assert_eq!(
                observations[0].event.body["generation"], 2,
                "local observation does not propose"
            );
            assert_eq!(observations[0].event.body["subject_hash"], json!(subject));
            assert!(lane.ports.repairs.lock().unwrap().is_empty());
        }
    }
}

#[tokio::test]
async fn resting_after_defeat_does_not_create_another_proposal() {
    let ports = FakePorts::new(CONTRACT_DRAFT).checks(&[StepOutcome::Fail]);
    choose(&ports, "rest", json!([]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    lane.wait_for("did_not_pass").await;
    let contract = lane.contract().await.unwrap();
    assert_eq!(contract.version, 3);
    assert!(contract.candidate.is_none());
    assert_eq!(lane.ledger.events().await.unwrap().len(), 3);
}

#[tokio::test]
async fn repeated_defeats_retire_only_their_proposals_without_losing_the_artifact() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Fail, StepOutcome::Fail, StepOutcome::Pass])
        .reviews(&[SUPPORT_REVIEW]);
    observe(&ports, "verify");
    observe(&ports, "verify");
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    lane.wait_for("supported").await;
    let contract = lane.contract().await.unwrap();
    assert_eq!(contract.version, 7);
    assert_eq!(contract.generation, 5);
    let journal = lane.ledger.experiments().await.unwrap();
    let verifications: Vec<_> = journal
        .iter()
        .filter(|r| r.event.kind == ExperimentKind::Verification)
        .collect();
    assert_eq!(
        verifications
            .iter()
            .map(|r| r.event.body["generation"].as_u64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 3, 5]
    );
    for event in verifications {
        assert_eq!(
            event.event.body["subject_hash"],
            json!(contract.candidate.unwrap().subject_hash)
        );
    }
    assert_eq!(
        *lane.ports.checked.lock().unwrap(),
        vec!["done", "done", "done"]
    );
}

#[tokio::test]
async fn root_failure_during_preparation_retains_work_for_strategy_without_a_delivery() {
    let gate = Arc::new(Notify::new());
    let mut ports = FakePorts::new(CONTRACT_DRAFT);
    ports.draft_gate = Some(gate.clone());
    choose(&ports, "rest", json!([]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("drafting").await;
    lane.runtime.turn_started().await;
    lane.write_answer("partial work");
    lane.runtime.turn_stopped("failed-root".into()).await;
    lane.runtime.thread_idle(ThreadIdleCause::Failed).await;
    assert_eq!(lane.status().await.unwrap().phase, "drafting");
    assert!(!lane.status().await.unwrap().resting);
    gate.notify_one();
    let status = lane.wait_for("not_verified").await;
    assert_eq!(status.class, "execution_interrupted");
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
    assert!(lane.ports.checked.lock().unwrap().is_empty());
    assert_eq!(
        lane.ledger.events().await.unwrap().len(),
        1,
        "capturing partial work must not Propose it"
    );
    let journal = lane.ledger.experiments().await.unwrap();
    assert!(
        !journal
            .iter()
            .any(|r| r.event.body["phase"] == "task_delivery")
    );
    assert!(
        journal
            .iter()
            .any(|r| r.event.body["phase"] == "interrupted_snapshot")
    );
    let handle = journal
        .iter()
        .find(|r| r.event.body["phase"] == "work_ready")
        .unwrap()
        .event
        .body["public_evidence"]
        .clone();
    let page = lane
        .runtime
        .evidence_access()
        .read(
            serde_json::from_value(json!({"op":"read", "handle":handle})).unwrap(),
            16384,
        )
        .await
        .unwrap();
    let retained: serde_json::Value = serde_json::from_str(page["text"].as_str().unwrap()).unwrap();
    assert_eq!(retained["assessment_scope"], "interruption");
    let prompts = lane.ports.continuation_prompts.lock().unwrap();
    let facts: serde_json::Value =
        serde_json::from_str(prompts[0].rsplit_once("\n\n").unwrap().1).unwrap();
    assert_eq!(facts["assessment"]["stage"], "interruption");
}

#[tokio::test]
async fn failed_root_can_select_local_work_without_an_extra_user_instruction() {
    let ports = FakePorts::new(CONTRACT_DRAFT);
    choose(&ports, "resume", json!(["R1"]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.runtime.turn_started().await;
    lane.write_answer("partial");
    lane.runtime.turn_stopped("failed-root".into()).await;
    lane.runtime.thread_idle(ThreadIdleCause::Failed).await;
    lane.wait_for("repairing").await;
    let repairs = lane.ports.repairs.lock().unwrap().clone();
    assert_eq!(repairs.len(), 1);
    assert_eq!(repairs[0].1, "failed-root");
    assert_eq!(lane.ledger.events().await.unwrap().len(), 1);
}

#[tokio::test]
async fn a_superseded_interruption_cannot_rest_or_dispatch_work_for_the_new_owner_turn() {
    let lane = lane(FakePorts::new(CONTRACT_DRAFT)).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    let old_epoch = lane.runtime.turn_started().await;
    lane.write_answer("old partial work");
    lane.runtime.turn_stopped("old-failure".into()).await;
    lane.runtime.turn_started().await;
    Arc::clone(&lane.runtime)
        .select_interrupted("late failure".into(), old_epoch)
        .await;
    assert_eq!(lane.status().await.unwrap().phase, "working");
    assert!(lane.ports.continuation_prompts.lock().unwrap().is_empty());
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
}

#[tokio::test]
async fn known_exhaustion_retains_the_handoff_without_starting_a_selector() {
    let mut lane = lane(FakePorts::new(CONTRACT_DRAFT)).await;
    let path = lane._dirs.path().join("resources.json");
    let write = |remaining: u64| {
        std::fs::write(&path,json!({
        "schema":"task_resources/v1","allocation_id":"shared-task","observed_at":1.0,
        "model_requests":{"limit":1,"reserved":1-remaining,"remaining":remaining},"time":{"deadline":null}
    }).to_string()).unwrap()
    };
    write(1);
    Arc::get_mut(&mut lane.runtime)
        .unwrap()
        .settings
        .resource_snapshot = Some(path.clone());
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    write(0);
    lane.executor_turn("delivery", "done").await;
    let status = lane.wait_for("not_verified").await;
    assert_eq!(status.class, "budget_exhausted");
    assert!(lane.ports.handoff_prompts.lock().unwrap().is_empty());
    assert!(lane.ports.checked.lock().unwrap().is_empty());
    assert_eq!(lane.ledger.events().await.unwrap().len(), 2);
    assert!(lane.contract().await.unwrap().support.is_none());
}

#[tokio::test]
async fn a_handoff_can_rest_without_scheduling_checks_review_or_support() {
    let ports = FakePorts::new(CONTRACT_DRAFT);
    ports.handoff_choices.lock().unwrap().push_back(json!({"choice":{
        "action":"rest","reason":"Deliver the captured partial work; further information is unavailable."
    }}).to_string());
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "partial work").await;
    lane.wait_for("not_verified").await;
    assert!(lane.ports.checked.lock().unwrap().is_empty());
    assert!(lane.ports.review_prompts.lock().unwrap().is_empty());
    assert_eq!(lane.ledger.events().await.unwrap().len(), 2);
    assert!(lane.contract().await.unwrap().support.is_none());
}

#[tokio::test]
async fn selected_checks_and_focused_diagnosis_reuse_receipts_without_acceptance() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[SUPPORT_REVIEW]);
    ports.handoff_choices.lock().unwrap().push_back(
        json!({"choice":{
            "action":"check","reason":"Observe the public build first."
        }})
        .to_string(),
    );
    ports.continuation_choices.lock().unwrap().extend([
        json!({"choice":{"action":"diagnose","question":"Does the captured answer contain the requested text?"}}).to_string(),
        json!({"choice":{"action":"rest","reason":"Local observation delivered; no acceptance claim requested by this strategy."}}).to_string(),
    ]);
    let lane = reference_lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    let status = lane.wait_for("not_verified").await;
    assert_eq!(lane.ports.checked.lock().unwrap().len(), 1);
    assert!(
        lane.ports
            .sealed_checked
            .lock()
            .unwrap()
            .iter()
            .all(Vec::is_empty)
    );
    let mut observation_errors = Vec::new();
    for record in lane.ledger.experiments().await.unwrap() {
        if record.event.body["phase"] == "work_assessed" {
            let handle: Digest =
                serde_json::from_value(record.event.body["public_evidence"].clone()).unwrap();
            let document: serde_json::Value =
                serde_json::from_slice(&lane.runtime.store.get(&handle).unwrap()).unwrap();
            observation_errors.push(document["review_error"].clone());
        }
    }
    assert_eq!(
        lane.ports.review_prompts.lock().unwrap().len(),
        1,
        "{status:?}; {observation_errors:?}"
    );
    assert!(
        lane.ports.review_contexts.lock().unwrap().iter().any(
            |value| value["question"] == "Does the captured answer contain the requested text?"
        )
    );
    assert!(lane.contract().await.unwrap().support.is_none());
    assert_eq!(lane.ledger.events().await.unwrap().len(), 2);
    assert_eq!(
        lane.runtime
            .state
            .lock()
            .await
            .contract
            .as_ref()
            .unwrap()
            .policy
            .min_sealed_qualified,
        Some(4),
        "local observations must not relax the original acceptance policy"
    );
    let records = lane.ledger.experiments().await.unwrap();
    assert_eq!(
        records
            .iter()
            .filter(|r| r.event.body["phase"] == "work_assessed")
            .count(),
        2
    );
    assert!(
        !records
            .iter()
            .any(|r| r.event.kind == ExperimentKind::Verification)
    );
}

#[tokio::test]
async fn diagnostic_receipt_reaches_formal_review_after_its_original_grant_is_spent() {
    use std::os::unix::fs::PermissionsExt;
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass, StepOutcome::Pass, StepOutcome::Pass])
        .reviews(&[
            "SPEND_COMPUTATION",
            "DIAGNOSTIC_COMPUTATION_TIMEOUT",
            "SUPPORT_FROM_RETAINED_RECEIPT",
        ]);
    observe(&ports, "diagnose");
    observe(&ports, "verify");
    let mut lane = lane(ports).await;
    let docker = lane._dirs.path().join("fixture-docker");
    // Scripted container transport; real inspection reservation, process receipt,
    // publication, controller selection and paged review capabilities are used.
    std::fs::write(
        &docker,
        r#"#!/usr/bin/env python3
import pathlib, sys
root=pathlib.Path(__file__).parent
if sys.argv[1]=='image':
    print('sha256:'+'0'*64)
elif sys.argv[1]=='run':
    count=root/'compute-count'
    n=int(count.read_text())+1 if count.exists() else 1
    count.write_text(str(n))
    if n==1:
        print('invalid original checker')
        sys.exit(1)
    print('original diagnostic bytes '*240+'complete observation')
elif sys.argv[1]!='rm':
    sys.exit(2)
"#,
    )
    .unwrap();
    std::fs::set_permissions(&docker, std::fs::Permissions::from_mode(0o755)).unwrap();
    let runtime = Arc::get_mut(&mut lane.runtime).unwrap();
    runtime.profile.check.docker = docker.to_string_lossy().into_owned();
    runtime.settings.evaluation = Some(runtime.profile.clone());
    runtime.settings.review_execution = Some(crate::ReviewExecution {
        max_calls: 1,
        timeout_secs: 10,
        max_output_bytes: 16384,
        image: None,
        resources: None,
    });
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    lane.wait_for("supported").await;
    assert_eq!(
        std::fs::read_to_string(lane._dirs.path().join("compute-count")).unwrap(),
        "2"
    );
    let contexts = lane.ports.review_contexts.lock().unwrap();
    let observed = contexts
        .iter()
        .find(|c| {
            c["receipt_source"].is_string()
                && c["document"]["stdout"]
                    .as_str()
                    .is_some_and(|out| out.ends_with("complete observation\n"))
        })
        .unwrap();
    assert!(observed["pages"].as_u64().unwrap() > 1);
    let digest = Digest::of(observed["document"].to_string().as_bytes());
    assert_eq!(
        observed["receipt_source"],
        json!(format!("receipt:{digest}"))
    );
    drop(contexts);
    let mut diagnosis = None;
    for event in lane.ledger.experiments().await.unwrap() {
        if event.event.body["phase"] == "work_assessed" {
            let handle =
                serde_json::from_value(event.event.body["public_evidence"].clone()).unwrap();
            let public: serde_json::Value =
                serde_json::from_slice(&lane.runtime.store.get(&handle).unwrap()).unwrap();
            if public["assessment_scope"] == "diagnosis" {
                diagnosis = Some(public);
            }
        }
    }
    let diagnosis = diagnosis.unwrap();
    assert!(diagnosis["review"].is_null());
    assert!(diagnosis["review_error"].is_string());
    assert_eq!(diagnosis["inspections"], json!([digest]));
    assert!(lane.contract().await.unwrap().support.is_some());
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
}

#[tokio::test]
async fn formal_verification_receives_the_selected_diagnosis_as_scoped_history() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass, StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW, SUPPORT_REVIEW]);
    ports.handoff_choices.lock().unwrap().push_back(
        json!({"choice":{"action":"diagnose","question":"Inspect one uncertainty."}}).to_string(),
    );
    observe(&ports, "verify");
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    lane.wait_for("supported").await;
    let contexts = lane.ports.review_contexts.lock().unwrap();
    assert_eq!(
        contexts
            .iter()
            .filter(|c| c["question"] == "Inspect one uncertainty.")
            .count(),
        1
    );
    let prior = contexts
        .iter()
        .find(|c| c["document"]["public"]["assessment_scope"] == "diagnosis")
        .unwrap();
    assert_eq!(prior["same_subject"], true);
    assert!(prior["operation_context"]["assessment_event"].is_string());
    assert_eq!(
        prior["operation_context"]["question"],
        "Inspect one uncertainty."
    );
    assert!(prior["handle"].is_string());
    assert_eq!(
        prior["current_subject_hash"],
        prior["document"]["subject_hash"]
    );
    assert_eq!(
        prior["document"]["public"]["review"]["verdict"]["type"],
        "cannot_judge"
    );
    assert_eq!(lane.ports.review_prompts.lock().unwrap().len(), 2);
    drop(contexts);
    assert!(lane.contract().await.unwrap().support.is_some());
}

#[tokio::test]
async fn prior_context_retains_old_subject_but_excludes_other_revisions_and_future_generations() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW]);
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("delivery", "done").await;
    lane.wait_for("not_verified").await;
    let contract = lane.contract().await.unwrap();
    let access = lane.runtime.evidence_access();
    let other_subject = Digest::of(b"another candidate");
    let context = access
        .latest_review_context(&contract, other_subject)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(context["same_subject"], false);
    assert_eq!(context["current_subject_hash"], json!(other_subject));
    assert_eq!(
        context["document"]["subject_hash"],
        json!(contract.candidate.unwrap().subject_hash)
    );
    let mut earlier = contract.clone();
    earlier.generation = 0;
    assert!(
        access
            .latest_review_context(&earlier, other_subject)
            .await
            .unwrap()
            .is_none()
    );
    let mut revised = contract.clone();
    revised.revision += 1;
    assert!(
        access
            .latest_review_context(&revised, other_subject)
            .await
            .unwrap()
            .is_none()
    );
    let mut foreign = contract.clone();
    foreign.id = codex_pro_contract::ContractId("another-task".into());
    assert!(
        access
            .latest_review_context(&foreign, other_subject)
            .await
            .is_err()
    );

    for (kind, owner, phase) in [
        (
            ExperimentKind::Execution,
            contract.id.clone(),
            "continuation_decision",
        ),
        (
            ExperimentKind::Verification,
            foreign.id.clone(),
            "verification",
        ),
    ] {
        lane.runtime
            .append_event(
                kind,
                None,
                json!({
                    "contract_id":owner,"generation":contract.generation,"phase":phase,
                    "subject_hash":contract.candidate.unwrap().subject_hash,
                    "public_evidence":context["handle"],"question":"A later unrelated focus."
                }),
            )
            .await
            .unwrap();
        assert_eq!(
            access
                .latest_review_context(&contract, other_subject)
                .await
                .unwrap()
                .unwrap(),
            context,
            "A later reference or foreign responsibility cannot reinterpret the original assessment."
        );
    }

    let mut corrupt = context["document"]["public"].clone();
    corrupt["subject_hash"] = json!(other_subject);
    let handle = lane
        .runtime
        .store
        .put(corrupt.to_string().as_bytes())
        .unwrap();
    lane.runtime
        .append_event(
            ExperimentKind::Verification,
            None,
            json!({
                "contract_id":contract.id,"generation":contract.generation,
                "subject_hash":contract.candidate.unwrap().subject_hash,"public_evidence":handle
            }),
        )
        .await
        .unwrap();
    assert!(
        access
            .latest_review_context(&contract, other_subject)
            .await
            .unwrap_err()
            .contains("verification binding")
    );
}

#[tokio::test]
async fn continuation_capabilities_match_review_only_and_executable_profiles() {
    for (reference, build, computation) in [
        (false, false, false),
        (false, false, true),
        (false, true, false),
        (true, false, false),
        (true, true, true),
    ] {
        let ports = FakePorts::new(CONTRACT_DRAFT);
        ports.handoff_choices.lock().unwrap().push_back(
            json!({"choice":{"action":"rest","reason":"Inspect capabilities only."}}).to_string(),
        );
        let mut lane = if reference {
            reference_lane(ports).await
        } else {
            lane(ports).await
        };
        if build {
            Arc::get_mut(&mut lane.runtime)
                .unwrap()
                .profile
                .check
                .build_command = Some("build".into());
        }
        if computation {
            Arc::get_mut(&mut lane.runtime)
                .unwrap()
                .settings
                .review_execution = Some(crate::ReviewExecution {
                max_calls: 2,
                timeout_secs: 10,
                max_output_bytes: 4096,
                image: None,
                resources: None,
            });
        }
        lane.runtime.intake(INTAKE.into()).await;
        lane.wait_for("working").await;
        lane.executor_turn("delivery", "done").await;
        lane.wait_for("not_verified").await;
        let prompt = lane.ports.handoff_prompts.lock().unwrap()[0].clone();
        let facts: serde_json::Value =
            serde_json::from_str(prompt.rsplit_once("\n\n").unwrap().1).unwrap();
        let operations = &facts["capabilities"]["handoff_observations"];
        assert_eq!(
            facts["capabilities"]["selector"]["evidence_tool"],
            "contract_read_evidence"
        );
        assert_eq!(
            facts["capabilities"]["selector"]["mutable_execution"],
            false
        );
        assert_eq!(facts["capabilities"]["selector"]["image_rendering"], false);
        assert!(
            operations["reviewer_computation_admission"]
                .as_str()
                .unwrap()
                .contains("This selector opens no computation grant")
        );
        assert!(facts["assessment"]["check_execution"].is_null());
        assert_eq!(facts["assessment"]["retained_computations"], json!([]));
        assert_eq!(
            operations["check"]["check_pipeline_executes_program_or_container"],
            build
        );
        assert_eq!(
            operations["verify"]["check_pipeline_executes_program_or_container"],
            reference || build
        );
        assert_eq!(
            operations["diagnose"]["reviewer_computation_configured"],
            computation
        );
        assert_eq!(
            operations["verify"]["reviewer_computation_configured"],
            computation
        );
        assert_eq!(
            operations["verify"]["withheld_executable_steps"],
            if reference { 4 } else { 0 }
        );
        assert!(!prompt.contains("--sealed-secret"));
        assert!(lane.ports.checked.lock().unwrap().is_empty());
        assert!(lane.contract().await.unwrap().support.is_none());
    }
}

#[tokio::test]
async fn continuation_exposes_current_receipts_and_separate_historical_review_identity() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass, StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW]);
    ports.handoff_choices.lock().unwrap().extend([
        json!({"choice":{"action":"verify","reason":"Assess the first artifact."}}).to_string(),
        json!({"choice":{"action":"check","reason":"Observe the changed artifact."}}).to_string(),
    ]);
    choose(&ports, "resume", json!(["R1"]));
    choose(&ports, "rest", json!([]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "first version").await;
    lane.wait_for("repairing").await;
    lane.executor_turn("second", "changed version").await;
    lane.wait_for("not_verified").await;
    let prompts = lane.ports.continuation_prompts.lock().unwrap();
    let first: serde_json::Value =
        serde_json::from_str(prompts[0].rsplit_once("\n\n").unwrap().1).unwrap();
    let current: serde_json::Value =
        serde_json::from_str(prompts.last().unwrap().rsplit_once("\n\n").unwrap().1).unwrap();
    assert_eq!(current["assessment"]["stage"], "checks");
    assert_eq!(current["assessment"]["same_subject"], true);
    assert_eq!(
        current["assessment"]["subject_hash"],
        current["subject_hash"]
    );
    assert_eq!(
        current["prior_review"]["subject_hash"],
        first["subject_hash"]
    );
    assert_eq!(current["prior_review"]["same_subject"], false);
    assert_eq!(
        current["prior_review"]["generation"],
        first["assessment"]["generation"]
    );
    assert_eq!(
        current["prior_review"]["evidence_policy_hash"],
        first["assessment"]["evidence_policy_hash"]
    );
    let retained: serde_json::Value = serde_json::from_slice(
        &lane
            .runtime
            .store
            .get(&serde_json::from_value(current["public_evidence"].clone()).unwrap())
            .unwrap(),
    )
    .unwrap();
    assert_eq!(
        current["assessment"]["pipeline_complete"],
        retained["pipeline_complete"]
    );
    assert_eq!(
        current["assessment"]["capture_boundary"],
        retained["capture_boundary"]
    );
    assert_eq!(current["assessment"]["retained_computations"], json!([]));
    assert!(
        current["assessment"]["check_execution"].is_null(),
        "fixture supplied no execution receipt"
    );
    drop(prompts);
    assert!(lane.contract().await.unwrap().support.is_none());
}

#[tokio::test]
async fn a_selected_check_can_guide_repair_before_formal_verification() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Fail, StepOutcome::Pass])
        .reviews(&[SUPPORT_REVIEW]);
    ports.handoff_choices.lock().unwrap().push_back(
        json!({"choice":{
            "action":"check","reason":"Inspect the captured artifact."
        }})
        .to_string(),
    );
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "wrong").await;
    lane.wait_for("repairing").await;
    assert!(lane.ports.review_prompts.lock().unwrap().is_empty());
    lane.executor_turn("second", "done").await;
    lane.wait_for("supported").await;
    let commands: Vec<_> = lane
        .ledger
        .events()
        .await
        .unwrap()
        .iter()
        .map(|e| e.command.command.kind())
        .collect();
    assert_eq!(
        commands,
        vec![
            codex_pro_contract::CommandKind::Issue,
            codex_pro_contract::CommandKind::Propose,
            codex_pro_contract::CommandKind::Propose,
            codex_pro_contract::CommandKind::Support
        ]
    );
}

#[tokio::test]
async fn interrupted_strategy_selection_keeps_the_assessment_and_spends_no_execution() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW]);
    ports
        .continuation_choices
        .lock()
        .unwrap()
        .push_back("INTERRUPTED".into());
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    let status = lane.wait_for("not_verified").await;
    assert_eq!(status.class, "execution_interrupted");
    assert_eq!(status.repairs_used, 0);
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
    assert!(lane.contract().await.unwrap().candidate.is_some());
    assert_eq!(lane.ledger.events().await.unwrap().len(), 2);
}

#[tokio::test]
async fn a_later_choice_sees_the_prior_intention_and_observation_capability() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass, StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW, CANNOT_JUDGE_REVIEW]);
    choose(&ports, "resume", json!(["R1"]));
    choose(&ports, "rest", json!([]));
    let mut lane = lane(ports).await;
    Arc::get_mut(&mut lane.runtime)
        .unwrap()
        .settings
        .repair_attempts = 2;
    Arc::get_mut(&mut lane.runtime)
        .unwrap()
        .profile
        .check
        .build_command = Some("observe-answer".into());
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    lane.wait_for("repairing").await;
    lane.runtime
        .append_event(
            codex_pro_contract_store::ExperimentKind::Execution,
            None,
            json!({
                "phase":"successor_returned","continuation":codex_pro_contract::Digest::ZERO,
                "exit":"handoff","handoff_turn_id":"unrelated-turn"
            }),
        )
        .await
        .unwrap();
    lane.executor_turn("second", "done").await;
    lane.wait_for("not_verified").await;
    let facts: serde_json::Value = {
        let prompts = lane.ports.continuation_prompts.lock().unwrap();
        serde_json::from_str(prompts[1].rsplit_once("\n\n").unwrap().1).unwrap()
    };
    assert_eq!(facts["previous_choice"]["choice"]["action"], "resume");
    assert_eq!(
        facts["previous_choice"]["choice"]["requirements"],
        json!(["R1"])
    );
    assert!(facts["previous_choice"]["public_evidence"].is_string());
    let execution = &facts["previous_choice"]["execution_records"][0]["event"];
    assert_eq!(
        facts["previous_choice"]["execution_records"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(execution["phase"], "continuation_submission");
    assert_eq!(execution["submitted"], true);
    assert_eq!(
        execution["continuation"],
        facts["previous_choice"]["record"]
    );
    assert!(facts["previous_choice"]["delivery"].is_null());
    assert_eq!(facts["assessed_turn_id"], "second");
    assert_eq!(
        facts["capabilities"]["executor"]["actions"],
        json!(["resume"])
    );
    assert_eq!(
        facts["capabilities"]["public_checks"][0]["command"],
        "observe-answer"
    );
    assert!(facts["capabilities"]["host_observation_scope"]["limits"].is_array());
    assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
    assert_eq!(lane.runtime.state.lock().await.repairs_used, 1);
}

#[tokio::test]
async fn evidence_gap_can_continue_without_manufacturing_a_defeat_or_new_issue() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass, StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW, SUPPORT_REVIEW]);
    choose(&ports, "resume", json!(["R1"]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    lane.wait_for("repairing").await;
    let contract = lane.contract().await.unwrap();
    assert!(contract.candidate.is_some());
    assert!(contract.support.is_none());
    assert!(
        lane.ports.repairs.lock().unwrap()[0]
            .0
            .contains("the output is not visible")
    );
    lane.executor_turn("second", "done").await;
    lane.wait_for("supported").await;
    let events = lane.ledger.events().await.unwrap();
    let commands: Vec<_> = events.iter().map(|e| e.command.command.kind()).collect();
    assert_eq!(
        commands,
        vec![
            codex_pro_contract::CommandKind::Issue,
            codex_pro_contract::CommandKind::Propose,
            codex_pro_contract::CommandKind::Propose,
            codex_pro_contract::CommandKind::Support
        ]
    );
    assert_eq!(lane.ports.continuation_prompts.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn strategy_can_rest_after_a_real_defect_without_spending_a_local_attempt() {
    let ports = FakePorts::new(CONTRACT_DRAFT).checks(&[StepOutcome::Fail]);
    choose(&ports, "rest", json!([]));
    let mut lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "almost").await;
    let status = lane.wait_for("did_not_pass").await;
    assert_eq!(status.repairs_used, 0);
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
    super::recovery_tests::restart(&mut lane).await;
    assert_eq!(lane.status().await.unwrap().repairs_used, 0);
    assert!(lane.contract().await.unwrap().candidate.is_none());
}

#[tokio::test]
async fn unresolved_work_reservation_survives_restart_without_a_defeat() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW]);
    choose(&ports, "resume", json!(["R1"]));
    let mut lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    lane.wait_for("repairing").await;
    // Simulate an older status write surviving a crash after durable reservation.
    let mut status = lane.status().await.unwrap();
    status.repairs_used = 0;
    lane.ledger
        .put_record(STATUS_KIND, &lane.thread_id.to_string(), &status)
        .await
        .unwrap();
    super::recovery_tests::restart(&mut lane).await;
    assert_eq!(lane.runtime.state.lock().await.repairs_used, 1);
    assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
    assert!(lane.contract().await.unwrap().support.is_none());
}

#[tokio::test]
async fn a_strategy_cannot_select_an_obligation_with_a_genuine_scope_dispute() {
    const DRAFT: &str = r#"{"decision":"contract","reason":"","requirements":[{"id":"R1","text":"answer.txt says done","source_quote":"make answer.txt say done","inferred":false},{"id":"R2","text":"An extra goal","source_quote":"make answer.txt say done","inferred":true}],"out_of_scope":[],"differential_cases":[],"candidate_tests":false}"#;
    const REVIEW: &str = r#"{"assessments":[{"requirement_id":"R1","observations":"","judgment":{"status":"unresolved","missing":"Need to inspect output."}}],"terms_gap":[{"element":"extra goal","reason":"The request only asks for answer.txt; it does not require the extra goal.","affected_requirements":["R2"],"discrepancy":{"kind":"added","request_quote":null}}]}"#;
    let ports = FakePorts::new(DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[REVIEW]);
    choose(&ports, "resume", json!(["R2"]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    let status = lane.wait_for("not_verified").await;
    assert_eq!(status.class, "terms_gap");
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
    let prompt = lane.ports.continuation_prompts.lock().unwrap()[0].clone();
    let facts: serde_json::Value =
        serde_json::from_str(prompt.rsplit_once("\n\n").unwrap().1).unwrap();
    assert_eq!(facts["allowed_requirements"], json!(["R1"]));
}

const ALL_DISPUTED_REVIEW: &str = r#"{"verdict":"support","coverage":[{"requirement_id":"R1","evidence":"answer.txt"}],"findings":[],"terms_gap":[{"element":"A separately interpreted action","reason":"The reviewer disputes whether the request adds an independent action.","affected_requirements":["R1"],"discrepancy":{"kind":"omitted","request_quote":"make answer.txt say done"}}],"missing":"","residual":""}"#;

async fn select_original_request(lane: &Lane) -> Digest {
    let admission = lane.runtime.state.lock().await.admission.unwrap();
    lane.ports.continuation_choices.lock().unwrap().push_back(json!({"choice":{
        "action":"resume","request_admission":admission,
        "next_step":"Inspect the original request and retained work without adding the disputed goal."
    }}).to_string());
    admission
}

#[tokio::test]
async fn a_terms_dispute_does_not_revoke_original_request_execution_or_grant_support() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[ALL_DISPUTED_REVIEW]);
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    let admission = select_original_request(&lane).await;
    lane.executor_turn("first", "done").await;
    let status = lane.wait_for("repairing").await;
    assert_eq!(status.repairs_used, 1);
    assert!(lane.contract().await.unwrap().support.is_none());
    let prompt = lane.ports.continuation_prompts.lock().unwrap()[0].clone();
    let facts: serde_json::Value =
        serde_json::from_str(prompt.rsplit_once("\n\n").unwrap().1).unwrap();
    assert_eq!(facts["allowed_requirements"], json!([]));
    assert_eq!(facts["request_scope"]["admission"], json!(admission));
    assert!(
        facts["capabilities"]["executor"]["actions"]
            .as_array()
            .unwrap()
            .contains(&json!("resume"))
    );
    assert_eq!(facts["assessment"]["decision"], "not_verified");
    assert!(
        facts["assessment"]["detail"]
            .as_str()
            .unwrap()
            .contains("termsGap")
    );
    let events = lane.ledger.experiments().await.unwrap();
    assert!(
        events
            .iter()
            .any(|e| e.event.kind == ExperimentKind::Verification
                && e.event.body["class"] == "terms_gap")
    );
    assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn original_request_can_use_a_successor_without_renewing_or_falling_back_to_local_work() {
    use crate::controller::succession_budget::{Budget, KIND};
    for limit in [None, Some(1)] {
        let ports = FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass, StepOutcome::Pass])
            .reviews(&[ALL_DISPUTED_REVIEW, ALL_DISPUTED_REVIEW]);
        let mut lane = lane(ports).await;
        let runtime = Arc::get_mut(&mut lane.runtime).unwrap();
        runtime.settings.succession = Some(crate::SuccessionSettings {
            max_sessions: limit,
            total_secs: Some(60),
        });
        runtime.settings.repair_attempts = 3;
        *lane.ports.successor_workspace.lock().unwrap() = Some(lane.workspace.clone());
        lane.ports
            .successor_answers
            .lock()
            .unwrap()
            .push_back("done".into());
        lane.runtime.intake(INTAKE.into()).await;
        lane.wait_for("working").await;
        let admission = lane.runtime.state.lock().await.admission.unwrap();
        lane.ports.continuation_choices.lock().unwrap().push_back(json!({"choice":{
            "action":"successor","request_admission":admission,"next_step":"Continue only the original request."
        }}).to_string());
        choose(&lane.ports, "rest", json!([]));
        let before: Budget = lane
            .ledger
            .record(KIND, &lane.thread_id.to_string())
            .await
            .unwrap()
            .unwrap();
        lane.executor_turn("original", "done").await;
        lane.wait_for("not_verified").await;
        let after: Budget = lane
            .ledger
            .record(KIND, &lane.thread_id.to_string())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(after.grant, before.grant);
        assert_eq!(after.started_ms, before.started_ms);
        assert_eq!(after.deadline_ms, before.deadline_ms);
        assert_eq!(after.launches, 1);
        assert!(after.pending.is_none());
        assert!(lane.contract().await.unwrap().support.is_none());
        assert_eq!(lane.ports.successor_packets.lock().unwrap().len(), 1);
        assert!(lane.ports.repairs.lock().unwrap().is_empty());
        let prompts = lane.ports.continuation_prompts.lock().unwrap();
        let first: serde_json::Value =
            serde_json::from_str(prompts[0].rsplit_once("\n\n").unwrap().1).unwrap();
        let last: serde_json::Value =
            serde_json::from_str(prompts.last().unwrap().rsplit_once("\n\n").unwrap().1).unwrap();
        assert_eq!(first["allowed_requirements"], json!([]));
        assert!(
            first["available_actions"]
                .as_array()
                .unwrap()
                .contains(&json!("successor"))
        );
        assert!(
            !last["available_actions"]
                .as_array()
                .unwrap()
                .contains(&json!("resume"))
        );
        assert_eq!(
            last["available_actions"]
                .as_array()
                .unwrap()
                .contains(&json!("successor")),
            limit.is_none()
        );
    }
}

#[tokio::test]
async fn original_request_work_can_rest_and_cannot_escape_owner_or_resource_fences() {
    for mode in ["rest", "owner-change", "cancel", "expired", "unadmitted"] {
        let gate = Arc::new(Notify::new());
        let mut ports = FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[ALL_DISPUTED_REVIEW]);
        if matches!(mode, "owner-change" | "cancel" | "expired") {
            ports.continuation_gate = Some(gate.clone());
        }
        let mut lane = lane(ports).await;
        Arc::get_mut(&mut lane.runtime).unwrap().settings.succession =
            Some(crate::SuccessionSettings {
                max_sessions: Some(0),
                total_secs: Some(60),
            });
        lane.runtime.intake(INTAKE.into()).await;
        lane.wait_for("working").await;
        if mode == "rest" {
            choose(&lane.ports, "rest", json!([]));
        } else if mode == "unadmitted" {
            lane.ports.continuation_choices.lock().unwrap().push_back(json!({"choice":{
                "action":"resume","request_admission":Digest::of(b"foreign request"),"next_step":"unauthorized"}}).to_string());
        } else {
            select_original_request(&lane).await;
        }
        lane.executor_turn("first", "done").await;
        if matches!(mode, "owner-change" | "cancel" | "expired") {
            // Handoff selection also publishes "deciding" but does not enter
            // continuation_gate. Synchronize with the exact suspended worker,
            // not a shared presentation status from an earlier operation.
            tokio::time::timeout(Duration::from_secs(5), async {
                while lane.ports.continuation_prompts.lock().unwrap().is_empty() {
                    tokio::time::sleep(Duration::from_millis(1)).await;
                }
            })
            .await
            .unwrap();
            if mode == "owner-change" {
                lane.runtime.turn_started().await;
            }
            if mode == "cancel" {
                lane.runtime.stop().await;
            }
            if mode == "expired" {
                let key = lane.thread_id.to_string();
                let mut budget: crate::controller::succession_budget::Budget = lane
                    .ledger
                    .record(crate::controller::succession_budget::KIND, &key)
                    .await
                    .unwrap()
                    .unwrap();
                budget.started_ms = 1;
                budget.deadline_ms = Some(60_001);
                lane.ledger
                    .put_record(crate::controller::succession_budget::KIND, &key, &budget)
                    .await
                    .unwrap();
            }
            gate.notify_one();
        }
        if matches!(mode, "rest" | "expired" | "unadmitted") {
            let status = lane.wait_for("not_verified").await;
            assert_eq!(lane.ports.continuation_prompts.lock().unwrap().len(), 1);
            if mode == "expired" {
                assert_eq!(
                    status.class,
                    "budget_exhausted",
                    "status={status:?}; handoff_prompts={}; continuation_prompts={}; submitted_repairs={}",
                    lane.ports.handoff_prompts.lock().unwrap().len(),
                    lane.ports.continuation_prompts.lock().unwrap().len(),
                    lane.ports.repairs.lock().unwrap().len()
                );
            } else {
                assert_eq!(status.class, "terms_gap");
            }
        } else {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        assert!(lane.ports.repairs.lock().unwrap().is_empty(), "{mode}");
        assert!(lane.contract().await.unwrap().support.is_none(), "{mode}");
    }
}

#[tokio::test]
async fn local_observations_do_not_erase_an_unrelated_scope_dispute() {
    const DRAFT: &str = r#"{"decision":"contract","reason":"","requirements":[{"id":"R1","text":"answer.txt says done","source_quote":"make answer.txt say done","inferred":false},{"id":"R2","text":"An extra goal","source_quote":"make answer.txt say done","inferred":true}],"out_of_scope":[],"differential_cases":[],"candidate_tests":false}"#;
    const REVIEW: &str = r#"{"assessments":[{"requirement_id":"R1","observations":"","judgment":{"status":"unresolved","missing":"Need output."}}],"terms_gap":[{"element":"extra goal","reason":"Not requested.","affected_requirements":["R2"],"discrepancy":{"kind":"added","request_quote":null}}]}"#;
    const LOCAL: &str = r#"{"assessments":[{"requirement_id":"R1","observations":"Locally inspected.","judgment":{"status":"unresolved","missing":"Need another observation."}}],"terms_gap":[]}"#;
    for action in ["check", "diagnose"] {
        let ports = FakePorts::new(DRAFT)
            .checks(&[StepOutcome::Pass, StepOutcome::Pass])
            .reviews(&[REVIEW, LOCAL]);
        ports.handoff_choices.lock().unwrap().extend([
        json!({"choice":{"action":"verify","reason":"Establish the current assessment."}})
            .to_string(),
        json!({"choice":if action=="check" { json!({"action":"check","reason":"Observe the changed artifact."}) } else { json!({"action":"diagnose","question":"Inspect only R1."}) }}).to_string(),
    ]);
        choose(&ports, "resume", json!(["R1"]));
        choose(&ports, "resume", json!(["R2"]));
        let mut lane = lane(ports).await;
        Arc::get_mut(&mut lane.runtime)
            .unwrap()
            .settings
            .repair_attempts = 2;
        lane.runtime.intake(INTAKE.into()).await;
        lane.wait_for("working").await;
        lane.executor_turn("first", "done").await;
        lane.wait_for("repairing").await;
        lane.executor_turn("second", "done").await;
        lane.wait_for("not_verified").await;
        assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
        let prompts = lane.ports.continuation_prompts.lock().unwrap();
        let facts: serde_json::Value =
            serde_json::from_str(prompts.last().unwrap().rsplit_once("\n\n").unwrap().1).unwrap();
        assert_eq!(
            facts["assessment"]["stage"],
            if action == "check" {
                "checks"
            } else {
                "diagnosis"
            }
        );
        assert_eq!(facts["allowed_requirements"], json!(["R1"]));
    }
}

#[tokio::test]
async fn owner_input_fences_a_pending_strategy_selection() {
    let gate = Arc::new(Notify::new());
    let mut ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW]);
    ports.continuation_gate = Some(gate.clone());
    choose(&ports, "resume", json!(["R1"]));
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    lane.wait_for("deciding").await;
    lane.runtime.turn_started().await;
    gate.notify_one();
    tokio::time::sleep(Duration::from_millis(25)).await;
    lane.runtime.stop().await;
    assert!(lane.ports.repairs.lock().unwrap().is_empty());
    assert_eq!(lane.runtime.state.lock().await.repairs_used, 0);
}
