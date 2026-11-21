use super::*;
use crate::controller::archive::{Operation, Query};
use pretty_assertions::assert_eq;
use serde_json::{Value, json};

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn actual_worker_without_capture_has_no_capture_metadata() {
    use core_test_support::{responses, test_codex::test_codex};
    let server = responses::start_mock_server().await;
    let reply = r#"{"fixture":true}"#;
    let mocked = responses::mount_sse_once(
        &server,
        responses::sse(vec![
            responses::ev_response_created("plain-response"),
            responses::ev_assistant_message("plain-message", reply),
            responses::ev_completed("plain-response"),
        ]),
    )
    .await;
    let test = test_codex()
        .with_config(|config| {
            config.model = Some("gpt-5.1".into());
            config.model_reasoning_effort =
                Some(codex_protocol::openai_models::ReasoningEffort::High);
            config.model_provider.supports_websockets = false;
        })
        .build(&server)
        .await
        .unwrap();
    let config =
        crate::workers::runtime::worker_config(&test.config, &WorkerSettings::default()).unwrap();
    let result = crate::workers::runtime::run_turn(&test.thread_manager, config, WorkerTurn {
        prompt:"Return fixture JSON".into(), schema:json!({"type":"object","properties":{"fixture":{"type":"boolean"}},"required":["fixture"],"additionalProperties":false}),
        trigger:"pro_contract_fixture",deadline:Duration::from_secs(60),evidence:None,archive:None,capture:None,resources:None,
    }).await.unwrap();
    assert_eq!(result, reply);
    let raw = mocked.single_request().body_json();
    for field in [
        "procontract_capture",
        "procontract_capture_thread",
        "procontract_capture_request",
    ] {
        assert!(raw["client_metadata"].get(field).is_none());
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn actual_selector_worker_capture_reaches_gateway_with_same_native_boundary() {
    use core_test_support::{responses, test_codex::test_codex};
    use std::io::{BufRead, BufReader, Write};
    use std::process::{Command, Stdio};

    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("capture-worker", "done").await;
    lane.wait_for("supported").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let handle: Digest = serde_json::from_value(
        index["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["assessment_scope"] == "verification")
            .unwrap()["handle"]
            .clone(),
    )
    .unwrap();
    let public: crate::controller::work::PublicEvidence =
        serde_json::from_str(&read(&lane, handle).await).unwrap();
    let access = lane
        .runtime
        .evidence_access()
        .with_selection_sources(
            handle,
            &public,
            public.subject_hash,
            public.delivery_message,
        )
        .unwrap();
    let counts = (
        lane.ledger.events().await.unwrap().len(),
        lane.ledger.experiments().await.unwrap().len(),
    );
    let root = tempfile::tempdir().unwrap();
    let output = std::env::var_os("NATIVE_CAPTURE_ARTIFACTS")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| root.path().join("artifacts"));
    let native = output.join("native");
    let wire = output.join("wire");
    std::fs::create_dir_all(&native).unwrap();
    std::fs::create_dir_all(&wire).unwrap();
    let server = responses::start_mock_server().await;
    let reply = r#"{"choice":{"action":"rest","reason":"fixture"}}"#;
    let mocked = responses::mount_sse_once(
        &server,
        responses::sse(vec![
            responses::ev_response_created("capture-response"),
            responses::ev_assistant_message("capture-message", reply),
            responses::ev_completed("capture-response"),
        ]),
    )
    .await;
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../scripts/test_procontract_selector_capture.py");
    let stderr = std::fs::File::create(output.join("gateway.stderr")).unwrap();
    let mut gateway = Command::new("python3")
        .arg("-B")
        .arg(script)
        .arg("--native-gateway")
        .arg(&native)
        .arg(&wire)
        .arg(format!("{}/v1", server.uri()))
        .args(["gpt-5.1", "high"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(stderr)
        .spawn()
        .unwrap();
    let mut url = String::new();
    BufReader::new(gateway.stdout.take().unwrap())
        .read_line(&mut url)
        .unwrap();
    assert!(
        url.starts_with("http://127.0.0.1:"),
        "gateway did not start: {url}"
    );
    let test = test_codex()
        .with_config(|config| {
            config.model = Some("gpt-5.1".into());
            config.model_reasoning_effort =
                Some(codex_protocol::openai_models::ReasoningEffort::High);
            config.model_provider.supports_websockets = false;
        })
        .build(&server)
        .await
        .unwrap();
    let mut config =
        crate::workers::runtime::worker_config(&test.config, &WorkerSettings::default()).unwrap();
    config.model_provider.base_url = Some(url.trim().into());
    config.model_provider.supports_websockets = false;
    let prompt = format!(
        "Select the next action.\n\n{}",
        json!({"contract_id":public.contract_id,"subject_hash":public.subject_hash,"available_actions":["rest"]})
    );
    let result = crate::workers::runtime::run_turn(&test.thread_manager, config,
        WorkerTurn { prompt:prompt.clone(), schema:json!({"type":"object","additionalProperties":false,
            "properties":{"choice":{"type":"object","additionalProperties":false,"properties":{"action":{"type":"string"},"reason":{"type":"string"}},"required":["action","reason"]}},"required":["choice"]}),
            trigger:"pro_contract_continuation",deadline:Duration::from_secs(60),evidence:None,
            archive:Some(access.clone()),capture:Some(crate::SelectorCaptureSettings { directory:native.clone(),max_entries:1000,max_bytes:1<<20 }),resources:None }
    ).await;
    gateway.stdin.take().unwrap().write_all(b"close\n").unwrap();
    let gateway_status = gateway.wait().unwrap();
    assert!(gateway_status.success());
    assert_eq!(result.unwrap(), reply);
    let received = mocked.single_request().body_json();
    let metadata = &received["client_metadata"];
    assert_eq!(metadata["procontract_capture_request"], "0");
    let capture = metadata["procontract_capture"].as_str().unwrap();
    let binding: Value = serde_json::from_slice(
        &std::fs::read(wire.join(format!("{capture}.0.binding.json"))).unwrap(),
    )
    .unwrap();
    assert_eq!(binding["initial"], true);
    let effective = std::fs::read(wire.join(format!(
        "{}.payload.json",
        binding["effective_payload_sha256"].as_str().unwrap()
    )))
    .unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&effective).unwrap(),
        received
    );
    let manifest: Value =
        serde_json::from_slice(&std::fs::read(native.join(format!("{capture}.json"))).unwrap())
            .unwrap();
    assert_eq!(manifest["prompt"], prompt);
    assert_eq!(binding["boundary"], manifest["boundary"]);
    let closures: Vec<Value> = std::fs::read_dir(&native)
        .unwrap()
        .map(|entry| {
            serde_json::from_slice::<Value>(&std::fs::read(entry.unwrap().path()).unwrap()).unwrap()
        })
        .filter(|body| body["schema"] == "native-selector-closure/v1")
        .collect();
    assert_eq!(closures.len(), 1);
    assert_eq!(closures[0]["shutdown_confirmed"], true);
    assert_eq!(closures[0]["capture"], capture);
    assert_eq!(
        counts,
        (
            lane.ledger.events().await.unwrap().len(),
            lane.ledger.experiments().await.unwrap().len()
        )
    );
    // The same worker cannot manufacture another ordinal-zero request, and a
    // drift before first generation is refused by the actual contributor.
    use codex_extension_api::{ModelRequestContributor, ModelRequestInput, ModelRequestKind};
    let probe_root = output.join("probes");
    std::fs::create_dir_all(&probe_root).unwrap();
    let grant = crate::SelectorCaptureSettings {
        directory: probe_root,
        max_entries: 1000,
        max_bytes: 1 << 20,
    };
    let probe = crate::controller::selector_capture::SelectorCapture::prepare(
        &grant,
        access.clone(),
        &prompt,
        &json!({"type":"object"}),
        "fixture",
        Some("gpt-5.1"),
        json!("high"),
    )
    .await
    .unwrap();
    let stale = crate::controller::selector_capture::SelectorCapture::prepare(
        &grant,
        access,
        &prompt,
        &json!({"type":"object"}),
        "fixture",
        Some("gpt-5.1"),
        json!("high"),
    )
    .await
    .unwrap();
    let mut captures = Vec::new();
    for ordinal in 0..2 {
        assert!(!probe.budget_exhausted("probe-worker").await);
        let mut metadata = None;
        probe.request(ModelRequestInput {
            kind: ModelRequestKind::Generation,
            thread_id: "probe-worker",
            client_metadata: &mut metadata,
            model: "gpt-5.1",
        });
        let metadata = metadata.unwrap();
        assert_eq!(metadata["procontract_capture_request"], ordinal.to_string());
        captures.push(metadata["procontract_capture"].clone());
    }
    assert_eq!(captures[0], captures[1]);
    lane.ledger
        .append_experiment(&codex_pro_contract_store::ExperimentEvent {
            kind: ExperimentKind::Intake,
            identities: Default::default(),
            body: json!({"control":"capture-drift"}),
        })
        .await
        .unwrap();
    assert!(stale.budget_exhausted("stale-worker").await);
    std::fs::write(
        output.join("READBACK.json"),
        serde_json::to_vec_pretty(&json!({"actual_native_worker":true,
        "local_fake_requests":1,"paid_model_requests":0,"gateway_before_send_binding":true,
        "effective_payload_sha256":binding["effective_payload_sha256"],"native_capture":capture,
        "source_event_counts_unchanged_before_deliberate_drift":true,"shutdown_confirmed":true,
        "retry_ordinals_distinct":true,"drift_before_first_generation_refused":true}))
        .unwrap(),
    )
    .unwrap();
}

#[tokio::test]
async fn native_archive_export_matches_python_without_changing_the_source() {
    use crate::{ArchiveCaptureLimits, SelectorArchiveInvocation, SelectorArchiveReader};
    use codex_pro_contract_store::{ExperimentEvent, Identities};
    use std::io::Write;
    use std::process::{Command, Stdio};

    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("native-export", "done").await;
    lane.wait_for("supported").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let handle: Digest = serde_json::from_value(
        index["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["assessment_scope"] == "verification")
            .unwrap()["handle"]
            .clone(),
    )
    .unwrap();
    let public: crate::controller::work::PublicEvidence =
        serde_json::from_str(&read(&lane, handle).await).unwrap();
    let invocation = SelectorArchiveInvocation {
        contract_id: public.contract_id.clone(),
        assessment: handle,
        selected_subject: public.subject_hash,
        delivery: public.delivery_message,
    };
    let expected = SelectorArchiveReader::observe_boundary(
        lane.ledger.clone(),
        lane.runtime.store.clone(),
        &invocation,
    )
    .await
    .unwrap();
    let bridge = SelectorArchiveReader::bind(
        lane.ledger.clone(),
        lane.runtime.store.clone(),
        &invocation,
        expected.clone(),
    )
    .await
    .unwrap();
    let private = lane.runtime.store.put(b"private unindexed source").unwrap();
    let counts = (
        lane.ledger.events().await.unwrap().len(),
        lane.ledger.experiments().await.unwrap().len(),
    );
    let snapshot = bridge
        .export(ArchiveCaptureLimits {
            max_entries: 1000,
            max_bytes: 1 << 20,
        })
        .await
        .unwrap();
    assert!(snapshot["blobs"].get(private.to_string()).is_none());
    assert_eq!(snapshot["boundary"], expected);
    assert!(
        bridge
            .export(ArchiveCaptureLimits {
                max_entries: 1000,
                max_bytes: 1
            })
            .await
            .is_err()
    );
    let mut wrong = expected.clone();
    wrong["selected_subject"] = json!(private);
    assert!(
        SelectorArchiveReader::bind(
            lane.ledger.clone(),
            lane.runtime.store.clone(),
            &invocation,
            wrong
        )
        .await
        .is_err()
    );

    let mut requests = vec![
        json!({"query":{"op":"sources"}}),
        json!({"query":{"op":"index"}}),
        json!({"query":{"op":"frontier"}}),
        json!({"query":{"op":"read","source_id":"assessment"}}),
        json!({"query":{"op":"read","handle":private}}),
        json!({"query":{"op":"read","source_id":"not-in-this-invocation"}}),
    ];
    for (digest, _) in snapshot["blobs"].as_object().unwrap() {
        requests.push(json!({"query":{"op":"read","handle":digest},"budget":400}));
        let first = bridge
            .query(json!({"op":"read","handle":digest}), 8192)
            .await
            .unwrap();
        requests.push(json!({"query":{"op":"read","handle":digest,"offset":first["next_offset"]}}));
    }
    let mut answers = Vec::new();
    for request in &requests {
        let budget = request["budget"].as_u64().unwrap_or(8192) as usize;
        answers.push(match bridge.query(request["query"].clone(), budget).await {
            Ok(value) => json!({"result":value}),
            Err(reason) => json!({"error":reason}),
        });
    }
    let output = std::env::var_os("NATIVE_ARCHIVE_ARTIFACTS")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| lane._dirs.path().join("export-control"));
    std::fs::create_dir_all(&output).unwrap();
    let raw = serde_json::to_vec(&snapshot).unwrap();
    let digest = Digest::of(&raw).to_string();
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../scripts/procontract_frozen_evidence.py");
    let input = requests
        .iter()
        .map(|r| format!("{r}\n"))
        .collect::<String>();
    let mut pids = Vec::new();
    for side in ["left", "right"] {
        let path = output.join(format!("{side}.json"));
        std::fs::write(&path, &raw).unwrap();
        let mut child = Command::new("python3")
            .arg("-B")
            .arg(&script)
            .arg(&path)
            .args(["--sha256", &digest])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        pids.push(child.id());
        child
            .stdin
            .take()
            .unwrap()
            .write_all(input.as_bytes())
            .unwrap();
        let result = child.wait_with_output().unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let observed: Vec<Value> = String::from_utf8(result.stdout.clone())
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(observed, answers);
        assert_eq!(std::fs::read(path).unwrap(), raw);
        std::fs::write(
            output.join(format!("{side}.responses.jsonl")),
            result.stdout,
        )
        .unwrap();
    }
    std::assert_ne!(pids[0], pids[1]);
    assert_eq!(
        counts,
        (
            lane.ledger.events().await.unwrap().len(),
            lane.ledger.experiments().await.unwrap().len()
        )
    );
    std::fs::write(
        output.join("READBACK.json"),
        serde_json::to_vec_pretty(&json!({
            "native_queries":answers.len(),"subprocesses":pids,"snapshot_sha256":digest,
            "native_python_equal":true,"source_event_counts_unchanged":true,
            "private_blob_exported":false,"model_calls":0,"historical_store_opens":0,
        }))
        .unwrap(),
    )
    .unwrap();
    // A later event invalidates the bound reader; it must not silently move forward.
    lane.ledger
        .append_experiment(&ExperimentEvent {
            kind: ExperimentKind::Intake,
            identities: Identities::default(),
            body: json!({"control":"after frozen boundary"}),
        })
        .await
        .unwrap();
    assert_eq!(
        bridge.query(json!({"op":"index"}), 8192).await.unwrap_err(),
        "selector archive boundary changed"
    );
}

async fn query(lane: &Lane, op: Operation, handle: Option<Digest>, offset: usize) -> Value {
    lane.runtime
        .evidence_access()
        .read(
            Query {
                source_id: None,
                op,
                handle,
                offset,
            },
            4096,
        )
        .await
        .unwrap()
}

async fn read(lane: &Lane, handle: Digest) -> String {
    let mut offset = 0;
    let mut text = String::new();
    loop {
        let page = query(lane, Operation::Read, Some(handle), offset).await;
        text.push_str(page["text"].as_str().unwrap());
        if page["eof"] == true {
            break;
        }
        let next = page["next_offset"].as_u64().unwrap() as usize;
        assert!(next > offset);
        offset = next;
    }
    assert_eq!(Digest::of(text.as_bytes()), handle);
    text
}

#[tokio::test]
async fn unresolved_inspection_blocks_support_and_repair_while_retaining_the_local_review() {
    const DEFEAT: &str = r#"{"verdict":"defeat","coverage":[],"findings":[{"requirement_id":"R1","location":"answer.txt","counterexample":"wrong answer"}],"terms_gap":[],"missing":"","residual":"fix answer.txt"}"#;
    for (review, corrupt) in [(SUPPORT_REVIEW, false), (DEFEAT, false), (DEFEAT, true)] {
        let gate = Arc::new(Notify::new());
        let mut ports = FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[review]);
        ports.review_gate = Some(gate.clone());
        let mut lane = lane(ports).await;
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
        lane.runtime.intake(INTAKE.into()).await;
        lane.wait_for("working").await;
        lane.executor_turn("first", "done").await;
        tokio::time::timeout(Duration::from_secs(5), async {
            while lane.ports.review_prompts.lock().unwrap().is_empty() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        let contract = lane.contract().await.unwrap();
        let grant = lane
            ._dirs
            .path()
            .join("pro_contract/work")
            .join(contract.id.0.replace(['/', '.'], "_"))
            .join(contract.generation.to_string())
            .join("candidate.review");
        assert!(grant.join("grant.json").exists());
        let action = grant.join("call-0/action");
        std::fs::create_dir_all(&action).unwrap();
        let receipt = crate::ExecutionReceipt {
            run_id: "interrupted-inspection".into(),
            binding: Digest::of(b"interrupted action"),
            state: crate::ExecutionState::Running,
            started_ms: 1,
            finished_ms: None,
            exit_code: None,
            shutdown_confirmed: false,
            stdout_digest: None,
            stderr_digest: None,
            truncated: false,
            detail: String::new(),
        };
        std::fs::write(
            action.join("execution.json"),
            if corrupt {
                b"broken journal".to_vec()
            } else {
                serde_json::to_vec(&receipt).unwrap()
            },
        )
        .unwrap();
        gate.notify_one();
        let status = lane.wait_for("not_verified").await;
        assert_eq!(status.class, "infrastructure");
        assert!(lane.ports.repairs.lock().unwrap().is_empty());
        assert!(lane.contract().await.unwrap().support.is_none());
        let index = query(&lane, Operation::Index, None, 0).await;
        let handle = serde_json::from_value(
            index["entries"]
                .as_array()
                .unwrap()
                .iter()
                .find(|e| e["assessment_scope"] == "verification")
                .unwrap()["handle"]
                .clone(),
        )
        .unwrap();
        let public: Value = serde_json::from_str(&read(&lane, handle).await).unwrap();
        assert!(public["review"].is_object());
        if !corrupt {
            assert_eq!(public["inspection_actions"][0]["run_id"], receipt.run_id);
            assert_eq!(public["inspection_actions"][0]["shutdown_confirmed"], false);
        }
    }
}

#[tokio::test]
async fn named_sources_are_frozen_per_invocation_and_do_not_expand_the_public_archive() {
    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.runtime.turn_started().await;
    lane.write_answer("done");
    lane.runtime
        .turn_stopped_with_message(
            "first".into(),
            Some("The current build and tests succeeded. 原始结果"),
        )
        .await;
    lane.runtime.thread_idle(ThreadIdleCause::Completed).await;
    lane.wait_for("supported").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let handle: Digest = serde_json::from_value(
        index["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["assessment_scope"] == "verification")
            .unwrap()["handle"]
            .clone(),
    )
    .unwrap();
    let public: crate::controller::work::PublicEvidence =
        serde_json::from_str(&read(&lane, handle).await).unwrap();
    let delivery = public.delivery_message.unwrap();
    let original = lane
        .runtime
        .evidence_access()
        .with_selection_sources(handle, &public, public.subject_hash, Some(delivery))
        .unwrap();
    let ask = |source: &str| {
        serde_json::from_value::<Query>(json!({"op":"read","source_id":source})).unwrap()
    };
    let first = original.read(ask("delivery"), 8192).await.unwrap();
    assert_eq!(first["handle"], json!(delivery));
    assert!(
        first["text"]
            .as_str()
            .unwrap()
            .contains("current build and tests succeeded")
    );

    let mut later = public.clone();
    later.subject_hash = Digest::of(b"later frozen candidate");
    later.host_observations = None;
    later.review = None;
    later.findings.clear();
    later.checks.clear();
    later.pipeline_complete = false;
    later.assessment_scope = Some("handoff".into());
    let later_message = lane
        .runtime
        .store
        .put(
            json!({"subject_hash":later.subject_hash,"text":"Later delivery"})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    later.delivery_message = Some(later_message);
    let later_handle = lane
        .runtime
        .store
        .put(serde_json::to_string(&later).unwrap().as_bytes())
        .unwrap();
    lane.runtime.append_event(ExperimentKind::Execution, None, json!({"phase":"work_assessed", "contract_id":later.contract_id.0,
        "generation":later.generation,"subject_hash":later.subject_hash,"public_evidence":later_handle})).await.unwrap();
    let new = original
        .clone()
        .with_selection_sources(
            later_handle,
            &later,
            later.subject_hash,
            Some(later_message),
        )
        .unwrap();
    assert_eq!(original.read(ask("delivery"), 8192).await.unwrap(), first);
    assert_eq!(
        new.read(ask("delivery"), 8192).await.unwrap()["handle"],
        json!(later_message)
    );
    assert!(new.source_bindings()["observations"].is_null());
    let wrong_subject = lane
        .runtime
        .evidence_access()
        .with_selection_sources(later_handle, &later, later.subject_hash, Some(delivery))
        .unwrap();
    assert!(
        wrong_subject
            .read(ask("delivery"), 8192)
            .await
            .unwrap_err()
            .contains("frozen subject binding")
    );
    let unlinked = lane
        .runtime
        .store
        .put(
            json!({"subject_hash":public.subject_hash,"text":"not task-public"})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    let not_public = lane
        .runtime
        .evidence_access()
        .with_selection_sources(handle, &public, public.subject_hash, Some(unlinked))
        .unwrap();
    assert!(not_public.read(ask("delivery"), 8192).await.is_err());
    assert!(
        original
            .read(
                serde_json::from_value(
                    json!({"op":"read","source_id":"delivery","handle":delivery})
                )
                .unwrap(),
                8192
            )
            .await
            .is_err()
    );
    assert!(original.read(ask("missing"), 8192).await.is_err());
    let mut foreign = public.clone();
    foreign.contract_id = ContractId("foreign".into());
    assert!(
        lane.runtime
            .evidence_access()
            .with_selection_sources(handle, &foreign, public.subject_hash, Some(delivery))
            .is_err()
    );
    assert!(
        lane.runtime
            .evidence_access()
            .source_bindings()
            .as_object()
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn selector_receives_and_reads_its_current_delivery_through_the_production_archive() {
    let mut ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[CANNOT_JUDGE_REVIEW]);
    ports.read_selection_delivery = true;
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.runtime.turn_started().await;
    lane.write_answer("done");
    lane.runtime
        .turn_stopped_with_message(
            "current".into(),
            Some("CURRENT-DELIVERY: retained public checks already ran."),
        )
        .await;
    lane.runtime.thread_idle(ThreadIdleCause::Completed).await;
    lane.wait_for("not_verified").await;
    let reads = lane.ports.selection_delivery_reads.lock().unwrap();
    assert_eq!(
        reads.len(),
        2,
        "handoff selection and post-review continuation both receive the actual source"
    );
    assert!(
        reads
            .iter()
            .all(|read| read["delivery"].to_string().contains("CURRENT-DELIVERY"))
    );
    assert_eq!(reads[0]["stage"], "handoff");
    assert_eq!(reads[1]["stage"], "verification");
    drop(reads);
    assert!(lane.contract().await.unwrap().support.is_none());
}

#[tokio::test]
async fn retained_inspection_index_includes_diagnostics_without_a_judgment_and_keeps_task_scope() {
    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    lane.wait_for("supported").await;
    let contract = lane.contract().await.unwrap();
    let access = lane.runtime.evidence_access();
    let record = access.review_history().await.unwrap().pop().unwrap();
    let wanted = Digest::of(b"diagnostic raw receipt");
    for variant in 0..5 {
        let mut public = record.clone();
        public.review = None;
        public.review_error = Some("review timed out after the computation finished".into());
        public.inspections = vec![if variant == 0 {
            wanted
        } else {
            Digest::of(&[variant])
        }];
        match variant {
            1 => public.revision += 1,
            2 => public.generation += 1,
            3 => public.subject_hash = Digest::of(b"other artifact"),
            4 => public.contract_id = codex_pro_contract::ContractId("other responsibility".into()),
            _ => {}
        }
        let publication = Digest::of(&[variant, 1]);
        let document = json!({"contract_id":public.contract_id.0,"subject_hash":public.subject_hash,
            "publication":publication,"scope":"progress_only","public":public});
        let handle = lane
            .runtime
            .store
            .put(document.to_string().as_bytes())
            .unwrap();
        lane.runtime.append_event(ExperimentKind::Execution, None, json!({"phase":"progress_assessed",
            "contract_id":public.contract_id.0,"subject_hash":public.subject_hash,"publication":publication,
            "public_evidence":handle})).await.unwrap();
    }
    assert_eq!(
        access
            .inspection_sources(&contract, record.subject_hash)
            .await
            .unwrap(),
        vec![wanted]
    );
    assert_eq!(
        access
            .latest_review_context(&contract, record.subject_hash)
            .await
            .unwrap()
            .unwrap()["document"]["public"]["review"],
        json!(record.review)
    );
    let mut foreign = contract.clone();
    foreign.id = codex_pro_contract::ContractId("another task".into());
    assert!(
        access
            .inspection_sources(&foreign, record.subject_hash)
            .await
            .is_err()
    );
}

#[tokio::test]
async fn computations_are_retained_with_the_task_but_unlinked_or_foreign_blobs_are_not_readable() {
    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "done").await;
    lane.wait_for("supported").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let handle: Digest = serde_json::from_value(
        index["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|e| e["assessment_scope"] == "verification")
            .unwrap()["handle"]
            .clone(),
    )
    .unwrap();
    let mut record: crate::controller::work::PublicEvidence =
        serde_json::from_str(&read(&lane, handle).await).unwrap();
    let document =
        json!({"subject_hash":record.subject_hash,"stdout":"checked frozen numeric data"})
            .to_string();
    let computed = lane.runtime.store.put(document.as_bytes()).unwrap();
    let foreign = lane
        .runtime
        .store
        .put(
            json!({"subject_hash":Digest::of(b"foreign subject"),"stdout":"foreign"})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    record.inspections = vec![computed, foreign];
    let public = lane
        .runtime
        .store
        .put(&serde_json::to_vec(&record).unwrap())
        .unwrap();
    lane.runtime.append_event(ExperimentKind::Verification,None,json!({"contract_id":record.contract_id.0,
        "generation":record.generation,"subject_hash":record.subject_hash,"public_evidence":public})).await.unwrap();
    assert_eq!(read(&lane, computed).await, document);
    lane.runtime.restore().await;
    assert_eq!(read(&lane, computed).await, document);
    let access = lane.runtime.evidence_access();
    assert!(
        access
            .read(
                Query {
                    source_id: None,
                    op: Operation::Read,
                    handle: Some(foreign),
                    offset: 0
                },
                4096
            )
            .await
            .is_err()
    );
    let unlinked = lane
        .runtime
        .store
        .put(
            json!({"subject_hash":record.subject_hash,"stdout":"not linked"})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    assert!(
        access
            .read(
                Query {
                    source_id: None,
                    op: Operation::Read,
                    handle: Some(unlinked),
                    offset: 0
                },
                4096
            )
            .await
            .is_err()
    );
}

#[tokio::test]
async fn public_evidence_survives_summary_bounds_restart_and_later_verifications() {
    let ports = FakePorts::new(CONTRACT_DRAFT).checks(&[StepOutcome::Fail; 5]);
    let detail = format!(
        "{}\noriginal counterexample 尾部\u{0}\n",
        "retained observation\n".repeat(180)
    );
    ports
        .public_steps
        .lock()
        .unwrap()
        .push_back(vec![StepReceipt::new("build", StepOutcome::Fail, &detail)]);
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "failed").await;
    lane.wait_for("repairing").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let handle: Digest = serde_json::from_value(
        index["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|e| e["assessment_scope"] == "verification")
            .unwrap()["handle"]
            .clone(),
    )
    .unwrap();
    let original = read(&lane, handle).await;
    let record: Value = serde_json::from_str(&original).unwrap();
    assert_eq!(record["checks"][0]["receipt"]["detail"], detail);
    for i in 0..4 {
        lane.executor_turn(&format!("later-{i}"), "still failed")
            .await;
        lane.wait_for("did_not_pass").await;
    }
    lane.runtime.restore().await;
    assert_eq!(read(&lane, handle).await, original);
    assert_eq!(query(&lane, Operation::Index, None, 0).await["total"], 10);
    assert_eq!(
        query(&lane, Operation::Frontier, None, 0).await["entries"][0]["step"],
        "build"
    );
    assert_eq!(
        lane.ports.checked.lock().unwrap().len(),
        5,
        "recall never executes a check"
    );
}

#[tokio::test]
async fn unrelated_green_check_cannot_erase_a_retained_counterexample_or_authorize_support() {
    let ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Fail, StepOutcome::Pass, StepOutcome::Pass])
        .reviews(&[SUPPORT_REVIEW, SUPPORT_REVIEW]);
    ports.public_steps.lock().unwrap().extend([
        vec![StepReceipt::new(
            "build",
            StepOutcome::Fail,
            "older public counterexample",
        )],
        vec![StepReceipt::new(
            "candidate_tests",
            StepOutcome::Pass,
            "narrow green batch",
        )],
        vec![StepReceipt::new(
            "build",
            StepOutcome::Pass,
            "original check replayed",
        )],
    ]);
    let lane = lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("failed", "first").await;
    lane.wait_for("repairing").await;
    lane.executor_turn("green-but-incomplete", "second").await;
    let blocked = lane.wait_for("not_verified").await;
    assert_eq!(blocked.class, "insufficient_evidence");
    assert!(lane.contract().await.unwrap().support.is_none());
    let frontier = query(&lane, Operation::Frontier, None, 0).await;
    assert_eq!(frontier["entries"][0]["step"], "build");
    lane.runtime.restore().await;
    lane.executor_turn("actual-replay", "done").await;
    lane.wait_for("supported").await;
    assert_eq!(query(&lane, Operation::Frontier, None, 0).await["total"], 0);
    assert_eq!(
        lane.contract().await.unwrap().standing,
        codex_pro_contract::Standing::Outstanding
    );
}

#[tokio::test]
async fn candidate_repair_continues_without_erasing_independent_evidence_blockage() {
    let mut ports = FakePorts::new(CONTRACT_DRAFT).checks(&[StepOutcome::Fail, StepOutcome::Pass]);
    ports.sealed_qualified = 0;
    let lane = reference_lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("failed-without-evidence", "failed")
        .await;
    let status = lane.wait_for("repairing").await;
    assert!(status.detail.contains("Acceptance remains blocked"));
    assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
    assert!(lane.ports.successor_packets.lock().unwrap().is_empty());
    let events = lane.ledger.events().await.unwrap();
    assert!(events.iter().any(|e| matches!(
        e.command.command,
        codex_pro_contract::Command::Defeat { .. }
    )));
    let journal = lane.ledger.experiments().await.unwrap();
    let verification = journal
        .iter()
        .find(|r| r.event.kind == ExperimentKind::Verification)
        .unwrap();
    assert_eq!(verification.event.body["verdict"], "defeat");
    assert_eq!(
        verification.event.body["evidence_blocker"]["class"],
        "insufficient_evidence"
    );
    assert!(events.iter().any(|e| matches!(e.command.command, codex_pro_contract::Command::Defeat { defeater, .. } if defeater == verification.hash)));
    lane.executor_turn("candidate-repaired", "done").await;
    assert_eq!(
        lane.wait_for("not_verified").await.class,
        "insufficient_evidence"
    );
    assert!(lane.contract().await.unwrap().support.is_none());
    assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn admission_and_delivery_survive_a_declined_draft_and_restart() {
    let lane = lane(FakePorts::new(DECLINED_DRAFT)).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("abstained").await;
    lane.executor_turn("delivered-without-contract", "done")
        .await;
    let events = lane.ledger.experiments().await.unwrap();
    let intake = events
        .iter()
        .find(|e| e.event.kind == ExperimentKind::Intake)
        .unwrap();
    let deliveries: Vec<_> = events
        .iter()
        .filter(|e| e.event.body["phase"] == "task_delivery")
        .collect();
    assert_eq!(deliveries.len(), 1);
    assert_eq!(deliveries[0].event.body["intake"], json!(intake.hash));
    assert!(lane.contract().await.is_none());
    lane.runtime.restore().await;
    lane.runtime.restore().await;
    lane.runtime
        .intake("must not renew the original admission".into())
        .await;
    lane.executor_turn("second-delivery", "revised").await;
    let events = lane.ledger.experiments().await.unwrap();
    assert_eq!(
        events
            .iter()
            .filter(|e| e.event.kind == ExperimentKind::Intake)
            .count(),
        1
    );
    assert_eq!(
        events
            .iter()
            .filter(|e| e.event.body["phase"] == "task_delivery")
            .count(),
        2
    );
}

#[tokio::test]
async fn calibration_failure_cannot_be_erased_by_later_green_candidate_checks() {
    let mut ports = FakePorts::new(CONTRACT_DRAFT)
        .checks(&[StepOutcome::Pass])
        .reviews(&[SUPPORT_REVIEW]);
    ports.observation_exit = 2;
    let lane = reference_lane(ports).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("green-but-uncalibrated", "done").await;
    let status = lane.wait_for("not_verified").await;
    assert!(status.detail.contains("0 successful"));
    assert!(lane.contract().await.unwrap().support.is_none());
    assert_eq!(lane.ports.reviews.lock().unwrap().len(), 1);
    let events = lane.ledger.experiments().await.unwrap();
    let calibration = events
        .iter()
        .find(|e| e.event.body["phase"] == "calibration")
        .unwrap();
    let issued = events
        .iter()
        .find(|e| e.event.kind == ExperimentKind::Issue)
        .unwrap();
    assert!(calibration.seq < issued.seq);
    assert!(issued.event.body["evidence_policy"]["preparation_failure"].is_string());
}

#[tokio::test]
async fn scoped_review_defeat_repairs_only_the_unaffected_requirement_and_never_settles_the_gap() {
    const DRAFT: &str = r#"{"decision":"contract","reason":"","requirements":[{"id":"R1","text":"answer.txt says done","source_quote":"make answer.txt say done","inferred":false},{"id":"R2","text":"preserve an extra evidence file","source_quote":"make answer.txt say done","inferred":true}],"out_of_scope":[],"differential_cases":[],"candidate_tests":false}"#;
    const DEFEAT: &str = r#"{"verdict":"defeat","coverage":[],"findings":[{"requirement_id":"R1","location":"answer.txt","counterexample":"says failed instead of done"}],"terms_gap":[{"element":"extra preservation","reason":"not requested","affected_requirements":["R2"]}],"missing":"","residual":"also invent an extra preservation requirement"}"#;
    const GREEN_WITH_GAP: &str = r#"{"verdict":"support","coverage":[{"requirement_id":"R1","evidence":"answer.txt"},{"requirement_id":"R2","evidence":"answer.txt"}],"findings":[],"terms_gap":[{"element":"extra preservation","reason":"not requested","affected_requirements":["R2"]}],"missing":"","residual":""}"#;
    let lane = lane(
        FakePorts::new(DRAFT)
            .checks(&[StepOutcome::Pass, StepOutcome::Pass])
            .reviews(&[DEFEAT, GREEN_WITH_GAP]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    let terms = lane.contract().await.unwrap().bindings.terms_hash;
    lane.executor_turn("failed", "failed").await;
    let status = lane.wait_for("repairing").await;
    assert!(status.detail.contains("R1") && !status.detail.contains("also invent"));
    assert_eq!(lane.ports.repairs.lock().unwrap().len(), 1);
    lane.executor_turn("repaired", "done").await;
    assert_eq!(lane.wait_for("not_verified").await.class, "terms_gap");
    let contract = lane.contract().await.unwrap();
    assert_eq!(contract.bindings.terms_hash, terms);
    assert!(contract.support.is_none());
}

#[tokio::test]
async fn evidence_reader_excludes_sealed_cases_and_rejects_foreign_or_arbitrary_blobs() {
    let lane = reference_lane(FakePorts::new(CONTRACT_DRAFT).checks(&[StepOutcome::Fail])).await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("first", "failed").await;
    lane.wait_for("repairing").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let handle: Digest = serde_json::from_value(
        index["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|e| e["assessment_scope"] == "verification")
            .unwrap()["handle"]
            .clone(),
    )
    .unwrap();
    let public = read(&lane, handle).await;
    assert!(!public.contains("--sealed-secret"));
    assert!(!public.contains("sealed:s1"));
    let arbitrary = lane.runtime.store.put(b"private host data").unwrap();
    let mut foreign: Value = serde_json::from_str(&public).unwrap();
    foreign["contract_id"] = json!("another-responsibility");
    let foreign = lane
        .runtime
        .store
        .put(&serde_json::to_vec(&foreign).unwrap())
        .unwrap();
    let mut foreign_event = lane
        .ledger
        .experiments()
        .await
        .unwrap()
        .into_iter()
        .find(|r| r.event.kind == ExperimentKind::Verification)
        .unwrap()
        .event;
    foreign_event.body["contract_id"] = json!("another-responsibility");
    foreign_event.body["public_evidence"] = json!(foreign);
    lane.ledger.append_experiment(&foreign_event).await.unwrap();
    let access = lane.runtime.evidence_access();
    for id in [arbitrary, foreign] {
        assert!(
            access
                .read(
                    Query {
                        source_id: None,
                        op: Operation::Read,
                        handle: Some(id),
                        offset: 0
                    },
                    4096
                )
                .await
                .is_err()
        );
    }
    assert!(serde_json::from_value::<Query>(json!({"op":"read", "handle":"/etc/passwd"})).is_err());
    assert!(
        access
            .read(
                Query {
                    source_id: None,
                    op: Operation::Read,
                    handle: Some(handle),
                    offset: 0
                },
                1
            )
            .await
            .is_err()
    );
    assert!(
        access
            .read(
                Query {
                    source_id: None,
                    op: Operation::Read,
                    handle: Some(handle),
                    offset: usize::MAX
                },
                4096
            )
            .await
            .is_err()
    );
}

#[tokio::test]
async fn a_late_old_failure_does_not_reopen_a_newer_passing_replay() {
    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Fail, StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("old", "failed").await;
    lane.wait_for("repairing").await;
    let old = lane
        .ledger
        .experiments()
        .await
        .unwrap()
        .into_iter()
        .find(|r| r.event.kind == ExperimentKind::Verification)
        .unwrap()
        .event;
    lane.executor_turn("new", "done").await;
    lane.wait_for("supported").await;
    // A superseded verification can arrive late; its candidate generation remains old.
    lane.ledger.append_experiment(&old).await.unwrap();
    assert_eq!(query(&lane, Operation::Frontier, None, 0).await["total"], 0);
}

#[tokio::test]
async fn unreadable_work_evidence_blocks_support_instead_of_forgetting_a_counterexample() {
    let lane = lane(
        FakePorts::new(CONTRACT_DRAFT)
            .checks(&[StepOutcome::Fail, StepOutcome::Pass])
            .reviews(&[SUPPORT_REVIEW]),
    )
    .await;
    lane.runtime.intake(INTAKE.into()).await;
    lane.wait_for("working").await;
    lane.executor_turn("old", "failed").await;
    lane.wait_for("repairing").await;
    let index = query(&lane, Operation::Index, None, 0).await;
    let hash = index["entries"][0]["handle"].as_str().unwrap();
    let path = lane
        ._dirs
        .path()
        .join("pro_contract/blobs")
        .join(&hash[..2])
        .join(hash);
    std::fs::write(path, "corrupted evidence").unwrap();
    lane.executor_turn("new", "done").await;
    let status = lane.wait_for("not_verified").await;
    assert_eq!(status.class, "infrastructure");
    assert!(lane.contract().await.unwrap().support.is_none());
}
