use super::ReviewEvidence;
use crate::BlobStore;
use crate::CapturePolicy;
use crate::Terms;
use crate::capture;
use codex_protocol::dynamic_tools::DynamicToolCallOutputContentItem;
use pretty_assertions::assert_eq;
use serde_json::Value;
use serde_json::json;

fn fixture(text: &str) -> (tempfile::TempDir, ReviewEvidence) {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    std::fs::write(workspace.join("hindsight.json"), text).unwrap();
    let store = BlobStore::open(&root.path().join("blobs")).unwrap();
    let subject = capture(
        &workspace,
        &CapturePolicy {
            version: 1,
            excluded_paths: vec![],
            max_file_bytes: 1 << 20,
            max_total_bytes: 1 << 24,
        },
        &store,
    )
    .unwrap();
    let evidence = ReviewEvidence::new(
        subject,
        store,
        &Terms {
            context: Vec::new(),
            intake_text: "Write hindsight.json".to_string(),
            requirements: vec![crate::Requirement {
                id: "R1".to_string(),
                text: "hindsight.json exists".to_string(),
                source_quote: "Write hindsight.json".to_string(),
                inferred: false,
            }],
            out_of_scope: vec![],
            process_constraints: vec![],
        },
        "build passed".to_string(),
    )
    .unwrap();
    (root, evidence)
}

fn projected_fixture() -> (tempfile::TempDir, ReviewEvidence, Value) {
    let (root, previous) = fixture("actor-written assertion");
    let workspace = root.path().join("workspace");
    std::fs::create_dir_all(workspace.join("files/app")).unwrap();
    std::fs::write(
        workspace.join("files/app/observation.json"),
        "actor-written assertion",
    )
    .unwrap();
    std::fs::write(
        workspace.join("observation.json"),
        "Observed operation finished with exit 0; this does not attest other operations.",
    )
    .unwrap();
    let subject = capture(
        &workspace,
        &CapturePolicy {
            version: 1,
            excluded_paths: vec![],
            max_file_bytes: 1 << 20,
            max_total_bytes: 1 << 24,
        },
        &previous.store,
    )
    .unwrap();
    let mut terms = previous.terms.clone();
    terms.process_constraints.push(crate::ProcessConstraint {
        text: "Perform the requested operation".into(),
        source_quote: "Perform the requested operation".into(),
    });
    let entry = subject
        .manifest
        .entries
        .iter()
        .find(|entry| entry.path == "observation.json")
        .unwrap();
    let crate::EntryKind::File { digest, .. } = &entry.kind else {
        panic!("file")
    };
    let context = json!({"kind":"host_observations_v1", "subject_hash":subject.subject_hash,
        "terms_hash":crate::digest_of("terms", &terms),"capture_projection":{
            "subject_hash":subject.subject_hash,"producer":codex_pro_contract::Digest::of(b"configured provider"),
            "projection":{"task_files":{"prefix":"files","task_root":"/"},
                "observations":[{"path":"observation.json","sha256":digest,"kind":"execution_observation", "scope":"One retained historical call and completion; not current artifact correctness."}]}}});
    let evidence = ReviewEvidence::new(
        subject,
        previous.store,
        &terms,
        "No executable checks".into(),
    )
    .unwrap();
    (root, evidence, context)
}

#[test]
fn trusted_projected_execution_source_is_usable_without_promoting_same_named_task_file() {
    let (_root, evidence, context) = projected_fixture();
    let mut evidence = evidence.with_host_observations(context).unwrap();
    assert!(
        evidence
            .prompt("review")
            .contains("producer-declared origin.kind is execution_observation")
    );
    document(&mut evidence, "human_request");
    document(&mut evidence, "contract_terms");
    let catalog = read(
        &mut evidence,
        json!({"op":"list","source_id":"","offset":0}),
    );
    let entries = catalog["entries"].as_array().unwrap();
    let host = entries
        .iter()
        .find(|entry| entry["path"] == "observation.json")
        .unwrap()
        .clone();
    let task = entries
        .iter()
        .find(|entry| entry["path"] == "/app/observation.json")
        .unwrap()
        .clone();
    assert_eq!(host["kind"], "host_observation");
    assert_eq!(task["kind"], "candidate");
    let (_, host_citation) = document(&mut evidence, host["source_id"].as_str().unwrap());
    let (_, task_citation) = document(&mut evidence, task["source_id"].as_str().unwrap());
    assert!(
        evidence
            .resolve_citation("process:0", host_citation.as_str().unwrap())
            .is_ok()
    );
    assert!(
        evidence
            .resolve_citation("process:0", task_citation.as_str().unwrap())
            .is_err()
    );
}

#[test]
fn projected_source_needs_subject_digest_and_explicit_execution_origin() {
    let (_root, evidence, mut context) = projected_fixture();
    context["capture_projection"]["projection"]["observations"][0]["sha256"] =
        json!(codex_pro_contract::Digest::ZERO);
    assert!(evidence.with_host_observations(context).is_err());
    let (_root, evidence, mut context) = projected_fixture();
    context["capture_projection"]["subject_hash"] = json!(codex_pro_contract::Digest::ZERO);
    assert!(evidence.with_host_observations(context).is_err());
    let (_root, evidence, mut context) = projected_fixture();
    context["capture_projection"]["projection"]["observations"][0]["kind"] =
        json!("artifact_observation");
    let mut evidence = evidence.with_host_observations(context).unwrap();
    document(&mut evidence, "human_request");
    document(&mut evidence, "contract_terms");
    let index = evidence
        .subject
        .manifest
        .entries
        .iter()
        .position(|entry| entry.path == "observation.json")
        .unwrap();
    let (_, citation) = document(&mut evidence, &format!("file:{index}"));
    assert!(
        evidence
            .resolve_citation("process:0", citation.as_str().unwrap())
            .is_err()
    );
}

// Existing provenance controls require every support binding to be valid. The
// production resolver also retains independent judgments when this strict check fails.
impl ReviewEvidence {
    fn strict_review(&self, message: &str) -> Result<String, super::WorkerError> {
        let resolved = self.resolve_review(message)?;
        match resolved.protocol_error() {
            Some(error) => Err(super::WorkerError::Malformed(error)),
            None => Ok(resolved.message),
        }
    }
}

fn read(evidence: &mut ReviewEvidence, request: Value) -> Value {
    let response = evidence.respond(request);
    assert!(response.success);
    let DynamicToolCallOutputContentItem::InputText { text } = &response.content_items[0] else {
        panic!("text");
    };
    assert!(text.len() <= super::MAX_REPLY);
    serde_json::from_str(text).unwrap()
}

fn document(evidence: &mut ReviewEvidence, id: &str) -> (String, Value) {
    let mut offset = 0;
    let mut result = String::new();
    loop {
        let page = read(
            evidence,
            json!({"op":"read","source_id":id,"offset":offset}),
        );
        result.push_str(page["text"].as_str().unwrap());
        if page["eof"] == true {
            return (result, page["citation_id"].clone());
        }
        offset = page["next_offset"].as_u64().unwrap();
    }
}

async fn checkpoint_fixture() -> (tempfile::TempDir, ReviewEvidence, crate::Ledger) {
    let (root, evidence) = fixture("artifact");
    let config = codex_state::SqliteConfig::new_for_testing(
        codex_utils_absolute_path::AbsolutePathBuf::from_absolute_path(root.path()).unwrap(),
    );
    let ledger = crate::Ledger::open(&config, root.path()).await.unwrap();
    let evidence = evidence.with_protocol_log(
        ledger.clone(),
        Default::default(),
        json!({
            "contract_id":"task","revision":1,"formal":false,"assessment_id":"one",
        }),
    );
    (root, evidence, ledger)
}

#[test]
fn bounded_catalog_pages_preserve_every_legacy_entry_and_cursor() {
    let (root, mut evidence) = fixture("artifact");
    for index in 0..80 {
        std::fs::write(
            root.path()
                .join("workspace")
                .join(format!("输入-{index:03}.txt")),
            "data",
        )
        .unwrap();
    }
    evidence.subject = capture(
        &root.path().join("workspace"),
        &CapturePolicy {
            version: 1,
            excluded_paths: vec![],
            max_file_bytes: 1 << 20,
            max_total_bytes: 1 << 24,
        },
        &evidence.store,
    )
    .unwrap();
    evidence.order = (0..evidence.subject.manifest.entries.len()).collect();
    let count = evidence.documents.len() + evidence.order.len();
    let legacy: Vec<Value> = (0..count)
        .map(|offset| {
            read(
                &mut evidence,
                json!({"op":"index","source_id":"","offset":offset}),
            )["entry"]
                .clone()
        })
        .collect();
    let (mut entries, mut offset, mut pages) = (Vec::new(), 0, 0);
    loop {
        let page = read(
            &mut evidence,
            json!({"op":"list","source_id":"","offset":offset}),
        );
        let rows = page["entries"].as_array().unwrap();
        assert!(rows.len() > 1);
        entries.extend(rows.iter().cloned());
        pages += 1;
        let next = page["next_offset"].as_u64().unwrap() as usize;
        assert_eq!(next, offset + rows.len());
        offset = next;
        if page["eof"] == true {
            break;
        }
    }
    assert_eq!(entries, legacy);
    assert!(pages > 1 && pages < count);
    let end = read(
        &mut evidence,
        json!({"op":"list","source_id":"","offset":offset}),
    );
    assert_eq!(end["entries"], json!([]));
    assert_eq!(end["eof"], true);
    assert!(
        !evidence
            .respond(json!({"op":"list","source_id":"","offset":usize::MAX}))
            .success
    );
}

#[test]
fn rejected_read_arguments_do_not_exhaust_the_evidence_page_allowance() {
    let (_root, mut evidence) = fixture("artifact");
    for _ in 0..super::MAX_READS + 1 {
        assert!(
            !evidence
                .respond(json!({"op":"read","source_id":"human_request"}))
                .success
        );
    }
    assert!(
        !evidence
            .respond(json!({"op":"read","source_id":"unknown","offset":0}))
            .success
    );
    assert_eq!(evidence.calls, 0);
    assert!(evidence.issued.is_empty());
    assert_eq!(
        document(&mut evidence, "human_request").0,
        evidence.terms.intake_text
    );
    assert_eq!(evidence.calls, 1);
    evidence.calls = super::MAX_READS - 1;
    assert!(
        evidence
            .respond(json!({"op":"read","source_id":"file:0","offset":0}))
            .success
    );
    assert!(
        !evidence
            .respond(json!({"op":"read","source_id":"file:0","offset":0}))
            .success
    );
    assert_eq!(evidence.calls, super::MAX_READS);
}

#[test]
fn compact_catalog_keeps_scope_and_exact_sources_without_repeated_request_text() {
    let (_root, evidence) = fixture("artifact");
    let mut terms = evidence.terms.clone();
    terms.requirements.push(crate::Requirement {
        id: "R2".into(),
        ..terms.requirements[0].clone()
    });
    terms.context.push(crate::RequestContext {
        kind: crate::ContextKind::Preference,
        text: "A concise report is preferred".into(),
        source_quote: terms.intake_text.clone(),
    });
    let mut evidence =
        ReviewEvidence::new(evidence.subject, evidence.store, &terms, "checks".into()).unwrap();
    let catalog: Value =
        serde_json::from_str(&document(&mut evidence, "contract_terms").0).unwrap();
    assert_eq!(catalog["source_quotes"], json!([terms.intake_text]));
    assert_eq!(catalog["human_request"]["source_id"], "human_request");
    assert!(catalog.get("intake_text").is_none());
    assert_eq!(
        catalog["terms_hash"],
        json!(crate::digest_of("terms", &terms))
    );
    for (row, obligation) in catalog["obligations"]
        .as_array()
        .unwrap()
        .iter()
        .zip(terms.obligations())
    {
        assert_eq!(row["id"], obligation.id);
        assert_eq!(row["scope"], json!(obligation.scope));
        assert_eq!(
            catalog["source_quotes"][row["source_quote_index"].as_u64().unwrap() as usize],
            obligation.source_quote
        );
    }
    assert_eq!(catalog["context"][0]["source_quote_index"], 0);
}

#[tokio::test]
async fn local_checkpoint_does_not_need_a_redundant_global_missing_field() {
    let (_root, evidence, ledger) = checkpoint_fixture().await;
    let request = json!({"checkpoint_id":"local","review":{
        "assessments":[{"requirement_id":"R1","observations":"The available observation is partial.",
            "judgment":{"status":"unresolved","missing":"Need an artifact observation."}}],"terms_gap":[]}});
    assert!(evidence.record_checkpoint(request.clone()).await.success);
    let event = ledger.experiments().await.unwrap().remove(0).event;
    let handle = serde_json::from_value(event.body["handle"].clone()).unwrap();
    let retained: Value = serde_json::from_slice(&evidence.store.get(&handle).unwrap()).unwrap();
    assert_eq!(retained["completion_certified"], false);
    assert_ne!(retained["review"]["verdict"]["type"], "support");
    assert!(
        retained
            .to_string()
            .contains("Need an artifact observation.")
    );
    let mut malformed = request;
    malformed["checkpoint_id"] = json!("invalid");
    malformed["review"]["assessments"][0]["judgment"]
        .as_object_mut()
        .unwrap()
        .remove("missing");
    assert!(!evidence.record_checkpoint(malformed).await.success);
    assert_eq!(ledger.experiments().await.unwrap().len(), 1);
}

#[test]
fn a_rejected_binding_preserves_independent_support_and_counterexamples() {
    let (_root, mut evidence) = fixture("artifact");
    for id in ["R2", "R3"] {
        evidence.terms.requirements.push(crate::Requirement {
            id: id.into(),
            ..evidence.terms.requirements[0].clone()
        });
    }
    evidence = ReviewEvidence::new(
        evidence.subject,
        evidence.store,
        &evidence.terms,
        "checks".into(),
    )
    .unwrap();
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, citation) = document(&mut evidence, "file:0");
    let review = json!({"assessments":[
        {"requirement_id":"R1","observations":"Some examples pass.","judgment":{
            "status":"defeated","findings":[{"location":"artifact","counterexample":"Required behavior is absent."}],"missing":""}},
        {"requirement_id":"R2","observations":"The file exists.","judgment":{"status":"supported","evidence":citation}},
        {"requirement_id":"R3","observations":"A separate claim.","judgment":{"status":"supported","evidence":format!("citation_id={}",citation.as_str().unwrap())}}
    ],"terms_gap":[]});
    let resolved = evidence.resolve_review(&review.to_string()).unwrap();
    assert_eq!(resolved.rejected.len(), 1);
    assert_eq!(resolved.rejected[0].requirement_id, "R3");
    let parsed = super::super::reviewer::parse(&resolved.message, &evidence.terms).unwrap();
    assert_eq!(parsed.observations.len(), 3);
    assert_eq!(parsed.actionable_findings().len(), 1);
    let super::super::reviewer::ReviewVerdict::Partial {
        coverage, missing, ..
    } = parsed.verdict
    else {
        panic!("a rejected claim cannot grant whole-task Support")
    };
    assert_eq!(coverage.len(), 1);
    assert_eq!(coverage[0].requirement_id, "R2");
    assert_eq!(missing.len(), 1);
    assert_eq!(missing[0].requirement_id, "R3");
    assert!(
        missing[0]
            .reason
            .contains("expected one issued citation_id")
    );

    // Ambiguous identities cannot be made legitimate by retaining selected rows.
    let mut duplicate = review;
    duplicate["assessments"][2]["requirement_id"] = json!("R2");
    assert!(evidence.resolve_review(&duplicate.to_string()).is_err());
}

#[tokio::test]
async fn a_checkpoint_retains_local_work_and_reports_rejected_support_idempotently() {
    let (_root, mut evidence, ledger) = checkpoint_fixture().await;
    evidence
        .terms
        .process_constraints
        .push(crate::ProcessConstraint {
            text: "Perform the requested action before delivery.".into(),
            source_quote: "Perform the requested action before delivery.".into(),
        });
    let protocol_log = evidence.protocol_log.take();
    evidence = ReviewEvidence::new(
        evidence.subject,
        evidence.store,
        &evidence.terms,
        "checks".into(),
    )
    .unwrap();
    evidence.protocol_log = protocol_log;
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, citation) = document(&mut evidence, "file:0");
    let request = json!({"checkpoint_id":"local","review":{"assessments":[
        {"requirement_id":"R1","observations":"Concrete local defect.","judgment":{
            "status":"defeated","findings":[{"location":"artifact","counterexample":"Wrong contents."}],"missing":""}},
        {"requirement_id":"process:0","observations":"Artifact is not a history receipt.","judgment":{"status":"supported","evidence":citation}}
    ],"terms_gap":[]}});
    let first = evidence.record_checkpoint(request.clone()).await;
    assert!(first.success);
    assert!(evidence.record_checkpoint(request).await.success);
    let events = ledger.experiments().await.unwrap();
    assert_eq!(events.len(), 1);
    let event = &events[0].event.body;
    assert_eq!(event["rejected_support"][0]["requirement_id"], "process:0");
    let handle = serde_json::from_value(event["handle"].clone()).unwrap();
    let retained: Value = serde_json::from_slice(&evidence.store.get(&handle).unwrap()).unwrap();
    assert_eq!(
        retained["review"]["verdict"]["findings"][0]["requirement_id"],
        "R1"
    );
    assert_eq!(retained["review"]["verdict"]["coverage"], json!([]));
    assert_eq!(retained["completion_certified"], false);
}

#[tokio::test]
async fn invalid_inspection_does_not_spend_an_execution_reservation() {
    let (root, mut evidence) = fixture("artifact");
    let directory = root.path().join("inspection");
    evidence = evidence
        .with_execution(
            crate::CheckEnvironment {
                inputs: vec![],
                docker: "/nonexistent-test-docker".into(),
                image: "pinned".into(),
                user: "1000:1000".into(),
                candidate_mount: "/candidate".into(),
                timeout_secs: 10,
                build_command: None,
                candidate_command: None,
            },
            crate::ReviewExecution {
                max_calls: 1,
                timeout_secs: 5,
                max_output_bytes: 4096,
                image: None,
                resources: None,
            },
            directory.clone(),
            std::time::Duration::from_secs(60),
        )
        .unwrap();
    let spec = serde_json::to_value(evidence.inspection_tool()).unwrap();
    assert!(spec.to_string().contains("^(file|baseline):"));
    let deadline = std::fs::read(directory.join("grant.json")).unwrap();
    for request in [
        json!({"source_ids":["file:0"],"requirements":[],"python":"print('wrong scope')"}),
        json!({"source_ids":["contract_terms"],"requirements":["R1"],"python":"print('not a file')"}),
        json!({"source_ids":["file:0"],"requirements":["invented"],"python":"print('wrong ID')"}),
    ] {
        assert!(
            !evidence
                .inspect(request, std::time::Duration::from_secs(30))
                .await
                .success
        );
        assert!(!directory.join("reservation-0.json").exists());
    }
    let _ = evidence
        .inspect(
            json!({"source_ids":["file:0"],"requirements":["R1"],"python":"print('admitted')"}),
            std::time::Duration::from_secs(30),
        )
        .await;
    assert!(directory.join("reservation-0.json").exists());
    assert_eq!(
        std::fs::read(directory.join("grant.json")).unwrap(),
        deadline
    );
    assert!(
        !evidence
            .inspect(
                json!({"source_ids":[],"requirements":["R1"],"python":"print('again')"}),
                std::time::Duration::from_secs(30)
            )
            .await
            .success
    );
}

#[tokio::test]
async fn checkpoints_resolve_citations_are_durable_and_do_not_relabel_changed_work() {
    let (_root, mut evidence, ledger) = checkpoint_fixture().await;
    document(&mut evidence, "human_request");
    document(&mut evidence, "contract_terms");
    let (_, citation) = document(&mut evidence, "file:0");
    let request = json!({"checkpoint_id":"one","review":{
        "assessments":[{"requirement_id":"R1","observations":"The file is present.",
            "judgment":{"status":"supported","evidence":citation}}],"terms_gap":[],"missing":""}});
    let first = evidence.record_checkpoint(request.clone()).await;
    assert!(first.success);
    assert!(evidence.record_checkpoint(request.clone()).await.success);
    assert_eq!(ledger.experiments().await.unwrap().len(), 1);
    let event = ledger.experiments().await.unwrap().remove(0).event;
    let handle = serde_json::from_value(event.body["handle"].clone()).unwrap();
    let retained: Value = serde_json::from_slice(&evidence.store.get(&handle).unwrap()).unwrap();
    assert_eq!(retained["completion_certified"], false);
    assert_eq!(retained["review"]["verdict"]["type"], "support");
    let key = retained["review"]["verdict"]["coverage"][0]["evidence"]
        .as_str()
        .unwrap();
    assert_eq!(retained["review"]["citations"][key]["quote"], "artifact");
    assert_eq!(
        retained["review"]["citations"][key]["subject_hash"],
        json!(evidence.subject.subject_hash)
    );
    let mut changed = request.clone();
    changed["review"]["assessments"][0]["observations"] = json!("changed work");
    assert!(!evidence.record_checkpoint(changed).await.success);
    // Recreate the evidence capability: receipt recall does not depend on its
    // previous citation counter or worker history, and performs no new action.
    let restored = ReviewEvidence::new(
        evidence.subject.clone(),
        evidence.store.clone(),
        &evidence.terms,
        "checks".into(),
    )
    .unwrap()
    .with_protocol_log(
        ledger.clone(),
        Default::default(),
        evidence.protocol_log.as_ref().unwrap().2.clone(),
    );
    assert!(restored.record_checkpoint(request).await.success);
    assert_eq!(ledger.experiments().await.unwrap().len(), 1);
}

#[tokio::test]
async fn invalid_checkpoint_does_not_discard_prior_valid_work_or_create_new_obligations() {
    let (_root, evidence, ledger) = checkpoint_fixture().await;
    let valid = json!({"checkpoint_id":"finding","review":{
        "assessments":[{"requirement_id":"R1","observations":"Local observation.",
            "judgment":{"status":"defeated","findings":[{"location":"artifact","counterexample":"observed wrong value"}],"missing":"other behavior unexamined"}}],
        "terms_gap":[],"missing":""}});
    assert!(evidence.record_checkpoint(valid.clone()).await.success);
    let mut invalid = valid;
    invalid["checkpoint_id"] = json!("invalid");
    invalid["review"]["assessments"][0]["requirement_id"] = json!("invented-obligation");
    assert!(!evidence.record_checkpoint(invalid).await.success);
    assert_eq!(ledger.experiments().await.unwrap().len(), 1);
    let unissued = json!({"checkpoint_id":"false-support","review":{
        "assessments":[{"requirement_id":"R1","observations":"claim",
            "judgment":{"status":"supported","evidence":"e99"}}],"terms_gap":[],"missing":""}});
    assert!(evidence.record_checkpoint(unissued).await.success);
    let events = ledger.experiments().await.unwrap();
    assert_eq!(events.len(), 2);
    let handle = serde_json::from_value(events[1].event.body["handle"].clone()).unwrap();
    let retained: Value = serde_json::from_slice(&evidence.store.get(&handle).unwrap()).unwrap();
    assert_eq!(retained["review"]["verdict"]["coverage"], json!([]));
    assert_eq!(
        retained["review"]["verdict"]["missing"][0]["requirement_id"],
        "R1"
    );
    assert_eq!(retained["review"]["verdict"]["type"], "partial");
}

#[test]
fn prior_diagnosis_is_context_and_cannot_be_cited_as_current_artifact_or_execution_proof() {
    let (_root, evidence) = fixture("new artifact");
    let mut evidence = evidence
        .with_diagnosis(Some("Investigate the remaining issue"))
        .with_prior_assessment(Some(json!({
            "subject_hash":"old-subject","review":{"verdict":"support"}
        })));
    document(&mut evidence, "human_request");
    document(&mut evidence, "contract_terms");
    let (_, prior) = document(&mut evidence, "prior_assessment");
    let response = json!({"assessments":[{"requirement_id":"R1","observations":"prior claim",
        "judgment":{"status":"supported","evidence":prior}}],"terms_gap":[],"missing":""});
    assert!(
        evidence
            .strict_review(&response.to_string())
            .unwrap_err()
            .to_string()
            .contains("not observed evidence")
    );
}

#[test]
fn runtime_capabilities_bind_history_without_promoting_it_to_current_evidence() {
    for relation in ["same", "different", "unknown"] {
        let (_root, evidence) = fixture("current artifact");
        let subject = evidence.subject.subject_hash;
        let prior_subject = match relation {
            "same" => json!(subject),
            "different" => json!(codex_pro_contract::Digest::of(b"older artifact")),
            _ => Value::Null,
        };
        let mut evidence = evidence.with_prior_assessment(Some(json!({
            "document":{"subject_hash":prior_subject,"public":{"review":{"verdict":"support"}}},
            "same_subject":true,
        })));
        let prompt = evidence.prompt("Review the frozen evidence.");
        let capabilities: Value = serde_json::from_str(
            prompt
                .rsplit_once("Runtime evidence capabilities: ")
                .unwrap()
                .1,
        )
        .unwrap();
        assert_eq!(capabilities, evidence.capabilities());
        assert_eq!(capabilities["subject_hash"], json!(subject));
        assert_eq!(
            capabilities["terms_hash"],
            json!(crate::digest_of("terms", &evidence.terms))
        );
        assert_eq!(
            capabilities["prior_assessment"]["subject_hash"],
            prior_subject
        );
        assert_eq!(
            capabilities["prior_assessment"]["same_subject"],
            match relation {
                "same" => json!(true),
                "different" => json!(false),
                _ => Value::Null,
            }
        );
        assert_eq!(
            capabilities["prior_assessment"]["current_support_citation"],
            false
        );
        for source in ["human_request", "contract_terms"] {
            document(&mut evidence, source);
        }
        let (_, prior_citation) = document(&mut evidence, "prior_assessment");
        let (_, current_citation) = document(&mut evidence, "file:0");
        assert!(
            evidence
                .resolve_citation("R1", prior_citation.as_str().unwrap())
                .is_err()
        );
        assert_eq!(
            evidence
                .resolve_citation("R1", current_citation.as_str().unwrap())
                .unwrap()
                .subject_hash,
            subject
        );
    }
}

#[tokio::test]
async fn runtime_capabilities_expose_text_only_reads_and_resolved_computation_grant() {
    let (root, mut evidence) = fixture("artifact");
    std::fs::write(
        root.path().join("workspace/hindsight.json"),
        b"\x89PNG\r\n\x1a\n",
    )
    .unwrap();
    evidence.subject = capture(
        &root.path().join("workspace"),
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    let before = evidence.capabilities();
    assert_eq!(before["read_evidence"]["encoding"], "utf8_text");
    assert_eq!(before["read_evidence"]["image_rendering"], false);
    assert!(before["inspect_evidence"].is_null());
    assert!(
        !evidence
            .respond(json!({"op":"read","source_id":"file:0","offset":0}))
            .success
    );
    let directory = root.path().join("inspection");
    evidence = evidence
        .with_execution(
            crate::CheckEnvironment {
                inputs: vec![],
                docker: "/unavailable-test-docker".into(),
                image: "check-image".into(),
                user: "1000:1000".into(),
                candidate_mount: "/candidate".into(),
                timeout_secs: 10,
                build_command: None,
                candidate_command: None,
            },
            crate::ReviewExecution {
                max_calls: 1,
                timeout_secs: 5,
                max_output_bytes: 4096,
                image: Some("review-image".into()),
                resources: None,
            },
            directory.clone(),
            std::time::Duration::ZERO,
        )
        .unwrap();
    let after = evidence.capabilities();
    assert_eq!(after["inspect_evidence"]["effective_image"], "review-image");
    assert_eq!(after["retained_computation_sources"], 0);
    assert_eq!(after["read_evidence"]["image_rendering"], false);
    let result = evidence.inspect(json!({"source_ids":["file:0"],"requirements":["R1"],"python":"print('binary computation')"}), std::time::Duration::from_secs(10)).await;
    assert!(
        !result.success,
        "an installed tool must not renew an expired grant"
    );
    assert!(!directory.join("reservation-0.json").exists());
    assert_eq!(
        evidence.capabilities(),
        after,
        "a refusal supplies no computation evidence"
    );
}

#[test]
fn formal_review_can_read_prior_context_without_inheriting_its_focus_or_verdict() {
    let (_root, evidence) = fixture("current artifact");
    let subject = evidence.subject.subject_hash;
    let prior = json!({"document":{"subject_hash":subject,
        "public":{"assessment_scope":"diagnosis","review":{"verdict":"defeat"},
        "evidence_policy_hash":"historical-method"}},"same_subject":true});
    let mut evidence = evidence.with_prior_assessment(Some(prior.clone()));
    assert!(!evidence.can_checkpoint());
    assert!(
        !evidence
            .documents
            .iter()
            .any(|(id, _)| id == "diagnosis_request")
    );
    assert!(
        !evidence
            .prompt("review")
            .contains("This is a diagnostic assessment.")
    );
    let (retained, prior_citation) = document(&mut evidence, "prior_assessment");
    assert_eq!(serde_json::from_str::<Value>(&retained).unwrap(), prior);
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, current_citation) = document(&mut evidence, "file:0");
    let current = json!({"assessments":[{"requirement_id":"R1","observations":"Current file exists.",
        "judgment":{"status":"supported","evidence":current_citation}}],"terms_gap":[]});
    assert!(evidence.strict_review(&current.to_string()).is_ok());
    let inherited = json!({"assessments":[{"requirement_id":"R1","observations":"Prior judgment.",
        "judgment":{"status":"supported","evidence":prior_citation}}],"terms_gap":[]});
    assert!(evidence.strict_review(&inherited.to_string()).is_err());
}

#[test]
fn oversized_optional_history_is_explicitly_unavailable_without_blocking_current_review() {
    let (_root, evidence) = fixture("current artifact");
    let prior = json!({"handle":"retained-history", "text":"x".repeat(super::MAX_FILE as usize)});
    let mut evidence = evidence.with_prior_assessment(Some(prior));
    let (prior, _) = document(&mut evidence, "prior_assessment");
    let unavailable: Value = serde_json::from_str(&prior).unwrap();
    assert_eq!(unavailable["availability"], "unavailable");
    assert_eq!(unavailable["retained_handle"], "retained-history");
    assert!(prior.len() < super::MAX_REPLY);
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, current) = document(&mut evidence, "file:0");
    let review = json!({"assessments":[{"requirement_id":"R1","observations":"Current file exists.",
        "judgment":{"status":"supported","evidence":current}}],"terms_gap":[]});
    assert!(evidence.strict_review(&review.to_string()).is_ok());
}

#[test]
fn execution_coverage_requires_scoped_host_evidence_and_rejects_self_attestation() {
    let (_root, previous) = fixture("artifact");
    let mut terms = previous.terms.clone();
    terms.process_constraints.push(crate::ProcessConstraint {
        text: "Access the feedback".into(),
        source_quote: "Access the feedback".into(),
    });
    let subject = previous.subject.clone();
    let mut evidence = ReviewEvidence::new(subject.clone(), previous.store, &terms, "checks".into()).unwrap()
        .with_delivery_message(json!({"kind":"native_final_response","subject_hash":subject.subject_hash,
            "complete":true,"text":"I read the feedback."})).unwrap()
        .with_host_observations(json!({"kind":"host_observations_v1","subject_hash":subject.subject_hash,
            "terms_hash":crate::digest_of("terms",&terms),"observation":"native tool completed with the recorded feedback bytes"})).unwrap();
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, artifact) = document(&mut evidence, "file:0");
    let (_, claim) = document(&mut evidence, "delivery_message");
    let (_, observed) = document(&mut evidence, "host_observations");
    let response = |citation: Value| {
        json!({"verdict":"support","coverage":[
        {"requirement_id":"R1","evidence":artifact},{"requirement_id":"process:0","evidence":citation}],
        "findings":[],"gaps":[],"terms_gap":[],"missing":"","residual":""}).to_string()
    };
    assert!(evidence.strict_review(&response(claim)).is_err());
    // Even a successful computation over artifact bytes cannot attest a past read.
    evidence.computations.push(
        json!({"execution":{"state":"completed","shutdown_confirmed":true,"truncated":false},
        "output_truncated":false,"requirements":["process:0"],"stdout":"feedback read"})
        .to_string(),
    );
    let (_, computation) = document(&mut evidence, "computation:0");
    assert!(evidence.strict_review(&response(computation)).is_err());
    assert!(evidence.strict_review(&response(observed)).is_ok());
    assert!(evidence.with_host_observations(json!({"kind":"host_observations_v1",
        "subject_hash":codex_pro_contract::Digest::of(b"foreign"),"terms_hash":crate::digest_of("terms",&terms)})).is_err());
}

#[test]
fn a_host_check_receipt_can_evidence_a_check_execution_obligation() {
    let (_root, previous) = fixture("artifact");
    let mut terms = previous.terms.clone();
    terms.process_constraints.push(crate::ProcessConstraint {
        text: "Run the configured check".into(),
        source_quote: "Run the configured check".into(),
    });
    let mut evidence = ReviewEvidence::new(
        previous.subject,
        previous.store,
        &terms,
        "Host checker action after capture: configured check completed successfully.".into(),
    )
    .unwrap();
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, artifact) = document(&mut evidence, "file:0");
    let (_, check) = document(&mut evidence, "check_receipts");
    let message = json!({"verdict":"support","coverage":[
        {"requirement_id":"R1","evidence":artifact},
        {"requirement_id":"process:0","evidence":check}],
        "findings":[],"gaps":[],"terms_gap":[],"missing":"","residual":""})
    .to_string();
    let resolved = evidence.strict_review(&message).unwrap();
    let value: Value = serde_json::from_str(&resolved).unwrap();
    let citation = &value["citations"][value["coverage"][1]["evidence"].as_str().unwrap()];
    assert_eq!(citation["source_id"], "check_receipts");
    assert!(
        citation["quote"]
            .as_str()
            .unwrap()
            .contains("after capture")
    );
}

#[test]
fn scoped_judgments_resolve_support_citations_without_promoting_observations() {
    let (_root, mut evidence) = fixture("artifact");
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (_, citation) = document(&mut evidence, "file:0");
    let mut message = json!({"assessments":[{
        "requirement_id":"R1", "observations":"Some local behavior is visible.",
        "judgment":{"status":"supported", "evidence":citation}
    }],"terms_gap":[],"missing":""});
    let resolved = evidence.strict_review(&message.to_string()).unwrap();
    let value: Value = serde_json::from_str(&resolved).unwrap();
    let bound = &value["citations"][value["coverage"][0]["evidence"].as_str().unwrap()];
    assert_eq!(bound["source_id"], "file:0");
    assert_eq!(
        value["observations"][0]["detail"],
        "Some local behavior is visible."
    );
    message["assessments"][0]["judgment"]["evidence"] = json!("e99999");
    assert!(evidence.strict_review(&message.to_string()).is_err());
    message["assessments"][0]["judgment"] = json!({"status":"defeated",
        "findings":[{"location":"artifact", "counterexample":"The boundary case fails."}], "missing":""});
    let parsed = super::super::reviewer::parse(
        &evidence.strict_review(&message.to_string()).unwrap(),
        &evidence.terms,
    )
    .unwrap();
    assert_eq!(parsed.actionable_findings().len(), 1);
    assert_eq!(parsed.observations.len(), 1);
}

#[test]
fn shared_citations_keep_repeated_support_compact_and_bound() {
    let (_root, previous) = fixture(&"x".repeat(4096));
    let mut terms = previous.terms.clone();
    terms.requirements = (0..19)
        .map(|i| crate::Requirement {
            id: format!("R{i}"),
            ..terms.requirements[0].clone()
        })
        .collect();
    let mut evidence =
        ReviewEvidence::new(previous.subject, previous.store, &terms, "checks".into()).unwrap();
    for source in ["human_request", "contract_terms"] {
        document(&mut evidence, source);
    }
    let (_, first) = document(&mut evidence, "file:0");
    let (_, second) = document(&mut evidence, "file:0");
    assert_ne!(first, second); // Different issued handles, same immutable range.
    let message = json!({"assessments": terms.requirements.iter().enumerate().map(|(i, r)| json!({
        "requirement_id":r.id,"observations":"", "judgment":{"status":"supported", "evidence":if i % 2 == 0 { &first } else { &second }}
    })).collect::<Vec<_>>(), "terms_gap":[]}).to_string();
    let resolved = evidence.strict_review(&message).unwrap();
    assert!(
        evidence.issued.last().unwrap()["quote"]
            .as_str()
            .unwrap()
            .len()
            * terms.requirements.len()
            > 65536
    );
    assert!(resolved.len() < 16_384);
    let review = super::super::reviewer::parse(&resolved, &terms).unwrap();
    let super::super::reviewer::ReviewVerdict::Support { coverage } = &review.verdict else {
        panic!("valid support must survive host rendering")
    };
    assert_eq!(coverage.len(), 19);
    assert_eq!(review.citations.len(), 1);
    let citation = &review.citations[&coverage[0].evidence];
    assert!(coverage.iter().all(|c| c.evidence == coverage[0].evidence));
    assert_eq!(citation.subject_hash, evidence.subject.subject_hash);
    assert_eq!(citation.terms_hash, crate::digest_of("terms", &terms));
    assert_eq!(citation.start, 0);
    assert_eq!(citation.end, 4096);
    assert_eq!(citation.quote, "x".repeat(4096));
    assert_eq!(
        coverage[0].evidence,
        crate::digest_of("review_citation", citation).to_string()
    );
    let mut other_terms = citation.clone();
    other_terms.terms_hash = codex_pro_contract::Digest::of(b"different terms");
    assert_ne!(
        coverage[0].evidence,
        crate::digest_of("review_citation", &other_terms).to_string()
    );
    let mut other_subject = citation.clone();
    other_subject.subject_hash = codex_pro_contract::Digest::of(b"different subject");
    assert_ne!(
        coverage[0].evidence,
        crate::digest_of("review_citation", &other_subject).to_string()
    );
}

#[test]
fn host_evidence_is_not_limited_as_model_output_and_survives_storage() {
    let original: String = (0..20)
        .map(|i| format!("{i:04}{}", "x".repeat(4092)))
        .collect();
    let (_root, previous) = fixture(&original);
    let mut terms = previous.terms.clone();
    terms.requirements = (0..20)
        .map(|i| crate::Requirement {
            id: format!("R{i}"),
            ..terms.requirements[0].clone()
        })
        .collect();
    let mut evidence =
        ReviewEvidence::new(previous.subject, previous.store, &terms, "checks".into()).unwrap();
    for source in ["human_request", "contract_terms"] {
        document(&mut evidence, source);
    }
    let assessments: Vec<_> = (0..20).map(|i| {
        let page = read(&mut evidence, json!({"op":"read","source_id":"file:0","offset":i*4096}));
        json!({"requirement_id":format!("R{i}"),"observations":"", "judgment":{"status":"supported","evidence":page["citation_id"]}})
    }).collect();
    let message = json!({"assessments":assessments,"terms_gap":[]}).to_string();
    assert!(message.len() < 65536);
    let resolved = evidence.strict_review(&message).unwrap();
    assert!(resolved.len() > 65536);
    let review = super::super::reviewer::parse(&resolved, &terms).unwrap();
    let handle = evidence
        .store
        .put(&serde_json::to_vec(&review).unwrap())
        .unwrap();
    let restored: super::super::reviewer::Review =
        serde_json::from_slice(&evidence.store.get(&handle).unwrap()).unwrap();
    assert_eq!(restored, review);
    assert_eq!(restored.citations.len(), 20);
    for citation in restored.citations.values() {
        assert_eq!(citation.quote, original[citation.start..citation.end]);
        assert_eq!(
            citation.sha256,
            codex_pro_contract::Digest::of(original.as_bytes())
        );
    }
    // A large model response remains outside the same original wire allowance.
    assert!(
        evidence
            .resolve_review(&format!("{message}{}", " ".repeat(65536)))
            .is_err()
    );
}

#[test]
fn changed_baseline_invalidates_only_receipts_that_used_that_source() {
    use codex_pro_contract::Digest;
    let (root, current) = fixture("current artifact");
    let original_dir = root.path().join("original");
    std::fs::create_dir(&original_dir).unwrap();
    std::fs::write(original_dir.join("signature.txt"), "original-A").unwrap();
    let before = capture(
        &original_dir,
        &CapturePolicy::standard(vec![]),
        &current.store,
    )
    .unwrap();
    std::fs::write(original_dir.join("signature.txt"), "original-B").unwrap();
    let after = capture(
        &original_dir,
        &CapturePolicy::standard(vec![]),
        &current.store,
    )
    .unwrap();
    let binding = Digest::of(b"unchanged current subject, terms and grant conditions");
    let input = |id: &str, source: &crate::Subject| {
        let crate::EntryKind::File { digest, len, .. } = &source.manifest.entries[0].kind else {
            panic!("file")
        };
        json!({"source_id":id,"source_subject_hash":source.subject_hash,
            "sha256":digest,"bytes":len,"path":format!("/evidence/{}",id.replace(':',"-"))})
    };
    let record = |schema, inputs| {
        json!({"schema":schema,"subject_hash":current.subject.subject_hash,
        "grant_binding":binding,"inputs":inputs,"stdout":"retained observation",
        "execution":{"state":"completed","shutdown_confirmed":true,"truncated":false},"output_truncated":false}).to_string()
    };
    let original_receipt = current
        .store
        .put(record(2, json!([input("baseline:0", &before)])).as_bytes())
        .unwrap();
    let mut old_current_input = input("file:0", &current.subject);
    old_current_input
        .as_object_mut()
        .unwrap()
        .remove("source_subject_hash");
    let current_receipt = current
        .store
        .put(record(1, json!([old_current_input])).as_bytes())
        .unwrap();
    let make = |baseline: crate::Subject| {
        ReviewEvidence::new(
            current.subject.clone(),
            current.store.clone(),
            &current.terms,
            "checks".into(),
        )
        .unwrap()
        .with_baseline(baseline)
        .with_retained_computations(Ok(vec![original_receipt, current_receipt]), Ok(binding))
    };
    let first = make(before.clone());
    assert_eq!(first.retained_computations.len(), 2);
    let mut changed = make(after);
    assert_eq!(changed.retained_computations.len(), 1);
    assert!(
        changed
            .retained_computations
            .contains(&format!("receipt:{current_receipt}"))
    );
    assert!(
        !changed
            .respond(
                json!({"op":"read","source_id":format!("receipt:{original_receipt}"),"offset":0})
            )
            .success
    );
    let (availability, _) = document(&mut changed, "retained_computation_availability");
    assert!(availability.contains("offered source version or bytes"));
    let downgraded = current
        .store
        .put(record(1, json!([input("baseline:0", &before)])).as_bytes())
        .unwrap();
    let rejected = ReviewEvidence::new(
        current.subject,
        current.store,
        &current.terms,
        "checks".into(),
    )
    .unwrap()
    .with_baseline(before)
    .with_retained_computations(Ok(vec![downgraded]), Ok(binding));
    assert!(
        rejected.retained_computations.is_empty(),
        "schema downgrade must not hide a baseline dependency"
    );
}

#[test]
fn archived_computations_keep_stable_sources_without_renewing_a_spent_grant() {
    use crate::inspection_grant::InspectionGrant;
    use std::time::Duration;
    let (root, evidence) = fixture("artifact");
    let environment = crate::CheckEnvironment {
        inputs: vec![],
        docker: "unused-docker".into(),
        image: "pinned-image".into(),
        user: "1000:1000".into(),
        candidate_mount: "/candidate".into(),
        timeout_secs: 30,
        build_command: None,
        candidate_command: None,
    };
    let limits = crate::ReviewExecution {
        max_calls: 1,
        timeout_secs: 10,
        max_output_bytes: 16384,
        image: None,
        resources: None,
    };
    let binding = InspectionGrant::binding_for(
        environment.clone(),
        limits.clone(),
        evidence.subject.subject_hash,
        &evidence.terms,
    )
    .unwrap();
    let grant_dir = root.path().join("formal");
    let grant = InspectionGrant::open(
        environment.clone(),
        limits.clone(),
        grant_dir.clone(),
        evidence.subject.subject_hash,
        &evidence.terms,
        Duration::from_secs(60),
    )
    .unwrap();
    let (work, _) = grant
        .reserve(&json!({"original":true}), Duration::from_secs(60))
        .unwrap();
    std::fs::create_dir_all(&work).unwrap();
    let original = json!({"subject_hash":evidence.subject.subject_hash,
        "execution":{"state":"failed","shutdown_confirmed":true,"truncated":false},
        "output_truncated":false,"stdout":"old checker failed"});
    let original = grant.retain(&work, original, &evidence.store).unwrap();
    let receipt = json!({"subject_hash":evidence.subject.subject_hash,"grant_binding":binding,
        "execution":{"state":"completed","shutdown_confirmed":true,"truncated":false},
        "output_truncated":false,"stdout":format!("{} mismatch=0", "独立观察\\\n".repeat(1500))})
    .to_string();
    let digest = evidence.store.put(receipt.as_bytes()).unwrap();
    let source = format!("receipt:{digest}");
    let before = std::fs::read(grant_dir.join("grant.json")).unwrap();
    let mut evidence = evidence
        .with_prior_assessment(Some(json!({"verdict":"support","receipt":digest})))
        .with_retained_computations(Ok(vec![digest, digest]), Ok(binding))
        .with_execution(
            environment,
            limits,
            grant_dir.clone(),
            Duration::from_secs(600),
        )
        .unwrap();
    assert_eq!(evidence.computations, vec![original]);
    assert_eq!(evidence.retained_computations.len(), 1);
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (text, citation) = document(&mut evidence, &source);
    assert_eq!(text, receipt);
    let resolved = evidence
        .resolve_citation("R1", citation.as_str().unwrap())
        .unwrap();
    assert_eq!(serde_json::to_value(resolved).unwrap()["source_id"], source);
    let (_, old) = document(&mut evidence, "computation:0");
    assert!(
        evidence
            .resolve_citation("R1", old.as_str().unwrap())
            .is_err()
    );
    let (_, prior) = document(&mut evidence, "prior_assessment");
    assert!(
        evidence
            .resolve_citation("R1", prior.as_str().unwrap())
            .is_err()
    );
    assert!(
        grant
            .reserve(&json!({"again":true}), Duration::from_secs(60))
            .unwrap_err()
            .contains("exhausted")
    );
    assert_eq!(before, std::fs::read(grant_dir.join("grant.json")).unwrap());
    assert_eq!(std::fs::read_dir(&grant_dir).unwrap().count(), 3);
    let later = evidence.store.put(b"new live tail").unwrap();
    let response =
        evidence.respond(json!({"op":"read","source_id":format!("receipt:{later}"),"offset":0}));
    assert!(
        !response.success,
        "only the frozen host allowlist is readable"
    );
}

#[test]
fn archived_computations_preserve_failures_and_reject_mismatched_conditions() {
    use codex_pro_contract::Digest;
    for (state, closed, truncated) in [
        ("completed", true, false),
        ("failed", true, false),
        ("running", false, false),
        ("completed", true, true),
    ] {
        let (_root, evidence) = fixture("artifact");
        let binding = Digest::of(b"host expected conditions");
        let receipt = json!({"subject_hash":evidence.subject.subject_hash,"grant_binding":binding,
            "execution":{"state":state,"shutdown_confirmed":closed,"truncated":truncated},
            "output_truncated":truncated,"stdout":"retained observation"})
        .to_string();
        let handle = evidence.store.put(receipt.as_bytes()).unwrap();
        let mut evidence = evidence.with_retained_computations(Ok(vec![handle]), Ok(binding));
        for id in ["human_request", "contract_terms"] {
            document(&mut evidence, id);
        }
        let (text, citation) = document(&mut evidence, &format!("receipt:{handle}"));
        assert_eq!(text, receipt);
        assert_eq!(
            evidence
                .resolve_citation("R1", citation.as_str().unwrap())
                .is_ok(),
            state == "completed" && closed && !truncated
        );
    }
    for changed_subject in [true, false] {
        let (_root, evidence) = fixture("artifact");
        let binding = Digest::of(b"current conditions");
        let receipt = json!({
            "subject_hash":if changed_subject { Digest::of(b"other artifact") } else { evidence.subject.subject_hash },
            "grant_binding":if changed_subject { binding } else { Digest::of(b"other inputs or conditions") },
            "stdout":"mismatch=0"}).to_string();
        let handle = evidence.store.put(receipt.as_bytes()).unwrap();
        let mut evidence = evidence.with_retained_computations(Ok(vec![handle]), Ok(binding));
        assert!(evidence.retained_computations.is_empty());
        let (text, _) = document(&mut evidence, "retained_computation_availability");
        assert!(text.contains("different subject, terms, inputs or computation conditions"));
        assert!(
            !evidence
                .respond(json!({"op":"read","source_id":format!("receipt:{handle}"),"offset":0}))
                .success
        );
        assert!(
            evidence
                .respond(json!({"op":"read","source_id":"file:0","offset":0}))
                .success
        );
    }
}

#[test]
fn computation_intent_is_not_an_artificial_requirement_boundary() {
    let (_root, original) = fixture("artifact");
    let mut terms = original.terms.clone();
    terms.requirements.push(crate::Requirement {
        id: "R2".into(),
        text: "The artifact has eight bytes".into(),
        source_quote: "The artifact has eight bytes".into(),
        inferred: false,
    });
    let mut evidence =
        ReviewEvidence::new(original.subject, original.store, &terms, "checks".into()).unwrap();
    for source in ["human_request", "contract_terms"] {
        document(&mut evidence, source);
    }
    let mut computation = json!({"subject_hash":evidence.subject.subject_hash,
        "requirements":["R1"], "execution":{"state":"completed", "shutdown_confirmed":true, "truncated":false},
        "output_truncated":false, "stdout":"artifact content and length=8"});
    evidence.computations.push(computation.to_string());
    let (_, citation) = document(&mut evidence, "computation:0");
    let response = json!({"assessments":[{"requirement_id":"R2", "observations":"Same observed bytes also establish their length.",
        "judgment":{"status":"supported", "evidence":citation}}], "terms_gap":[], "missing":""}).to_string();
    assert!(evidence.strict_review(&response).is_ok());
    computation["subject_hash"] = json!(codex_pro_contract::Digest::of(b"another subject"));
    evidence.computations[0] = computation.to_string();
    let error = evidence.strict_review(&response).unwrap_err().to_string();
    assert!(
        error.contains("R2")
            && error.contains(citation.as_str().unwrap())
            && error.contains("another frozen subject")
    );
    computation["subject_hash"] = json!(evidence.subject.subject_hash);
    computation["output_truncated"] = json!(true);
    evidence.computations[0] = computation.to_string();
    assert!(
        evidence
            .strict_review(&response)
            .unwrap_err()
            .to_string()
            .contains("truncated")
    );
}

#[test]
fn delivery_response_is_subject_bound_and_truncated_text_cannot_support_coverage() {
    let (_root, evidence) = fixture("artifact");
    assert!(evidence.with_delivery_message(json!({
        "kind":"native_final_response", "subject_hash":codex_pro_contract::Digest::of(b"foreign"),
        "complete":true, "text":"foreign response"
    })).is_err());
    for complete in [false, true] {
        let (_root, evidence) = fixture("artifact");
        let response = json!({"kind":"native_final_response", "subject_hash":evidence.subject.subject_hash,
            "complete":complete, "text":"Requested final explanation, not independent proof."});
        let mut evidence = evidence.with_delivery_message(response.clone()).unwrap();
        for id in ["human_request", "contract_terms"] {
            document(&mut evidence, id);
        }
        let (text, citation) = document(&mut evidence, "delivery_message");
        assert_eq!(serde_json::from_str::<Value>(&text).unwrap(), response);
        let review =
            json!({"verdict":"support", "coverage":[{"requirement_id":"R1","evidence":citation}],
            "findings":[],"terms_gap":[],"missing":"","residual":""})
            .to_string();
        assert_eq!(evidence.strict_review(&review).is_ok(), complete);
    }
}

#[tokio::test]
async fn frozen_binary_computation_has_scoped_citations_and_cannot_write_or_claim_failed_checks() {
    let Ok(image) = std::env::var("PRO_CONTRACT_TEST_IMAGE") else {
        return;
    };
    let (root, mut evidence) = fixture("placeholder");
    let workspace = root.path().join("workspace");
    let generated = std::process::Command::new("python3")
        .args([
            "-c",
            r#"
import io,struct,sys,zipfile
header=b"{'descr': '<f8', 'fortran_order': False, 'shape': (3,), }\n"
npy=b'\x93NUMPY\x01\x00'+struct.pack('<H',len(header))+header+struct.pack('<3d',1.,2.,3.)
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:z.writestr('x.npy',npy)
sys.stdout.buffer.write(out.getvalue())
"#,
        ])
        .output()
        .unwrap();
    assert!(generated.status.success());
    std::fs::write(workspace.join("hindsight.json"), generated.stdout).unwrap();
    evidence.subject = capture(
        &workspace,
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    // The computation must see the captured version, not this mutable workspace.
    std::fs::write(workspace.join("hindsight.json"), "changed after freeze").unwrap();
    std::fs::write(workspace.join("live-after-capture.txt"), "not an input").unwrap();
    let environment = crate::CheckEnvironment {
        inputs: Vec::new(),
        docker: "docker".into(),
        image,
        user: "1000:1000".into(),
        candidate_mount: "/candidate".into(),
        timeout_secs: 30,
        build_command: None,
        candidate_command: None,
    };
    let grant = crate::ReviewExecution {
        max_calls: 2,
        timeout_secs: 10,
        max_output_bytes: 4096,
        image: None,
        resources: None,
    };
    evidence = evidence
        .with_execution(
            environment,
            grant,
            root.path().join("review"),
            std::time::Duration::from_secs(60),
        )
        .unwrap();
    let code = r#"import ast,struct,zipfile
from pathlib import Path
data=zipfile.ZipFile('/evidence/file-0').read('x.npy')
assert data[:8]==b'\x93NUMPY\x01\x00'
n=struct.unpack('<H',data[8:10])[0]
header=ast.literal_eval(data[10:10+n].decode())
assert header['descr']=='<f8' and header['shape']==(3,)
assert struct.unpack('<3d',data[10+n:])==(1.,2.,3.)
try:
 Path('/evidence/file-0').write_bytes(b'changed')
 raise AssertionError('frozen evidence was writable')
except PermissionError: pass
except OSError as error: assert error.errno==30
assert not Path('/workspace/hindsight.json').exists()
assert not Path('/workspace/live-after-capture.txt').exists()
assert not Path('/sources/current/live-after-capture.txt').exists()
Path('/tmp/result').write_text('scratch is writable')
print('sum=6; original frozen bytes; isolated writable scratch')
"#;
    let response = evidence
        .inspect(
            json!({"source_ids":["file:0"],"requirements":["R1"],"python":code}),
            std::time::Duration::from_secs(20),
        )
        .await;
    assert!(response.success, "{response:?}");
    for id in ["human_request", "contract_terms"] {
        document(&mut evidence, id);
    }
    let (text, citation) = document(&mut evidence, "computation:0");
    let result: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(result["execution"]["state"], "completed", "{result}");
    assert!(result["stdout"].as_str().unwrap().contains("sum=6"));
    let supported = |citation: Value| {
        json!({"verdict":"support","coverage":[{"requirement_id":"R1","evidence":citation}],
        "findings":[],"terms_gap":[],"missing":"","residual":""})
        .to_string()
    };
    assert!(evidence.strict_review(&supported(citation)).is_ok());
    let response = evidence.inspect(json!({"source_ids":[],"requirements":["R1"],"python":"assert False, 'counterexample'"}),std::time::Duration::from_secs(20)).await;
    assert!(response.success);
    let (text, citation) = document(&mut evidence, "computation:1");
    let failed: Value = serde_json::from_str(&text).unwrap();
    assert_ne!(failed["execution"]["state"], "completed");
    assert!(evidence.strict_review(&supported(citation)).is_err());
    let refused = evidence
        .inspect(
            json!({"source_ids":[],"requirements":["R1"],"python":"print('third')"}),
            std::time::Duration::from_secs(20),
        )
        .await;
    assert!(!refused.success);
}

#[test]
fn frozen_data_is_paged_and_support_references_resolve_without_model_hashes() {
    let (root, mut evidence) = fixture(&"\"中文\\\n".repeat(1000));
    let original = std::fs::read_to_string(root.path().join("workspace/hindsight.json")).unwrap();
    std::fs::write(
        root.path().join("workspace/hindsight.json"),
        "changed later",
    )
    .unwrap();
    let mut restored = String::new();
    let mut offset = 0;
    let handle = loop {
        let chunk = read(
            &mut evidence,
            json!({"op":"read","source_id":"file:0","offset":offset}),
        );
        restored.push_str(chunk["text"].as_str().unwrap());
        if chunk["eof"] == true {
            break chunk["citation_id"].clone();
        }
        offset = chunk["next_offset"].as_u64().unwrap();
    };
    assert_eq!(restored, original);
    for source in ["human_request", "contract_terms"] {
        let chunk = read(
            &mut evidence,
            json!({"op":"read","source_id":source,"offset":0}),
        );
        assert_eq!(chunk["eof"], true);
    }
    let result = evidence.strict_review(&json!({"verdict":"support","findings":[],"terms_gap":[],"missing":"","residual":"","coverage":[{"requirement_id":"R1","evidence":handle}]}).to_string()).unwrap();
    let result: Value = serde_json::from_str(&result).unwrap();
    let cited = &result["citations"][result["coverage"][0]["evidence"].as_str().unwrap()];
    assert_eq!(cited["source_id"], "file:0");
    assert_eq!(
        cited["quote"].as_str().unwrap(),
        &original
            [cited["start"].as_u64().unwrap() as usize..cited["end"].as_u64().unwrap() as usize]
    );
}

#[test]
fn arbitrary_paths_writes_and_unissued_or_request_only_citations_are_refused() {
    let (_root, mut evidence) = fixture(&"\"中文\\\n".repeat(1000));
    for request in [
        json!({"op":"read","source_id":"/etc/passwd","offset":0}),
        json!({"op":"write","source_id":"file:0","offset":0}),
        json!({"op":"read","source_id":"file:0","offset":0,"path":"/etc/passwd"}),
    ] {
        assert!(!evidence.respond(request).success);
    }
    for handle in ["e0", "file:0", "e99999"] {
        assert!(evidence.strict_review(&json!({"verdict":"support","findings":[],"terms_gap":[],"missing":"","residual":"","coverage":[{"requirement_id":"R1","evidence":handle}]}).to_string()).is_err());
    }
    let chunk = read(
        &mut evidence,
        json!({"op":"read","source_id":"human_request","offset":0}),
    );
    assert!(evidence.strict_review(&json!({"verdict":"support","findings":[],"terms_gap":[],"missing":"","residual":"","coverage":[{"requirement_id":"R1","evidence":chunk["citation_id"]}]}).to_string()).is_err());
}

#[test]
fn prompt_is_bounded_and_documents_are_accessible_without_dumping_artifacts() {
    let (_root, mut evidence) = fixture(&"\"中文\\\n".repeat(1000));
    assert!(
        evidence
            .prompt(crate::workers::reviewer::DEFAULT_INSTRUCTIONS)
            .len()
            < 4096
    );
    let request = read(
        &mut evidence,
        json!({"op":"read","source_id":"human_request","offset":0}),
    );
    assert_eq!(request["text"], "Write hindsight.json");
    let checks = read(
        &mut evidence,
        json!({"op":"read","source_id":"check_receipts","offset":0}),
    );
    assert_eq!(checks["text"], "build passed");
}

#[test]
fn equal_length_original_versions_are_readable_against_one_fixed_candidate() {
    let (root, current) = fixture("fn f(x: u32) -> u32 { x }\n");
    let current_subject = current.subject.subject_hash;
    let mut comparisons = Vec::new();
    let mut original_lengths = Vec::new();
    for (index, ty) in ["u32", "i32"].into_iter().enumerate() {
        let directory = root.path().join(format!("original-{index}"));
        std::fs::create_dir(&directory).unwrap();
        let original = format!(
            "fn f(x: {ty}) -> {ty} {{ todo!() }}\n// {}",
            "保留的原文\n".repeat(1000)
        );
        original_lengths.push(original.len());
        std::fs::write(directory.join("hindsight.json"), &original).unwrap();
        let baseline =
            capture(&directory, &CapturePolicy::standard(vec![]), &current.store).unwrap();
        let baseline_hash = baseline.subject_hash;
        let mut evidence = ReviewEvidence::new(
            current.subject.clone(),
            current.store.clone(),
            &current.terms,
            "checks".into(),
        )
        .unwrap()
        .with_baseline(baseline);
        std::fs::write(directory.join("hindsight.json"), "mutable later file").unwrap();
        let catalog = read(
            &mut evidence,
            json!({"op":"list","source_id":"baseline","offset":0}),
        );
        let entry = &catalog["entries"][0];
        assert_eq!(entry["source_subject_hash"], json!(baseline_hash));
        assert_eq!(entry["assessed_subject_hash"], json!(current_subject));
        assert_eq!(entry["kind"], "baseline");
        assert_eq!(entry["current_support_citation"], false);
        let id = entry["source_id"].as_str().unwrap().to_owned();
        let mut offset = 0;
        let mut complete = String::new();
        let mut pages = 0;
        loop {
            let page = read(
                &mut evidence,
                json!({"op":"read","source_id":id,"offset":offset}),
            );
            assert_eq!(page["source_subject_hash"], json!(baseline_hash));
            assert_eq!(page["assessed_subject_hash"], json!(current_subject));
            assert!(page["citation_id"].is_null());
            complete.push_str(page["text"].as_str().unwrap());
            pages += 1;
            if page["eof"] == true {
                break;
            }
            offset = page["next_offset"].as_u64().unwrap();
        }
        assert!(pages > 1);
        assert_eq!(complete, original);
        let (candidate, _) = document(&mut evidence, "file:0");
        comparisons.push(complete.split('{').next() == candidate.split('{').next());
        let current_entry = read(
            &mut evidence,
            json!({"op":"index","source_id":"","offset":3}),
        );
        assert_eq!(
            current_entry["entry"]["matches_original_entry"], false,
            "neither metadata equality nor byte length distinguishes the intended signature"
        );
        assert!(
            !evidence
                .respond(json!({"op":"read","source_id":"baseline:00","offset":0}))
                .success
        );
        assert!(
            !evidence
                .respond(json!({"op":"read","source_id":"baseline:../hindsight.json","offset":0}))
                .success
        );
    }
    assert_eq!(comparisons, vec![true, false]);
    assert_eq!(original_lengths[0], original_lengths[1]);
}

#[test]
fn baseline_reads_require_host_binding_and_preserve_symlink_boundaries() {
    let (root, mut evidence) = fixture("current");
    assert!(
        !evidence
            .respond(json!({"op":"list","source_id":"baseline","offset":0}))
            .success
    );
    assert!(
        !evidence
            .respond(json!({"op":"read","source_id":"baseline:0","offset":0}))
            .success
    );
    let directory = root.path().join("original");
    std::fs::create_dir(&directory).unwrap();
    std::fs::write(directory.join("data.txt"), "original").unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink("data.txt", directory.join("link")).unwrap();
    let baseline = capture(
        &directory,
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    let crate::EntryKind::File { digest, .. } = baseline.manifest.entries[0].kind else {
        panic!("file")
    };
    evidence = evidence.with_baseline(baseline);
    assert_eq!(
        evidence.file_bytes("baseline:0", 1024).unwrap(),
        b"original"
    );
    #[cfg(unix)]
    assert!(
        evidence
            .file_bytes("baseline:1", 1024)
            .unwrap_err()
            .contains("links are not followed")
    );
    let digest = digest.to_string();
    std::fs::write(
        root.path().join("blobs").join(&digest[..2]).join(&digest),
        "changed blob",
    )
    .unwrap();
    assert!(evidence.file_bytes("baseline:0", 1024).is_err());
    assert_eq!(evidence.file_bytes("file:0", 1024).unwrap(), b"current");
}

#[tokio::test]
#[ignore = "requires an explicitly selected local Python image"]
async fn baseline_and_current_computation_preserves_both_source_subjects() {
    let image = std::env::var("PRO_CONTRACT_TEST_IMAGE").expect("select a local test image");
    let (root, evidence) = fixture("fn f(x: u32) -> u32 { x }\n");
    let original_dir = root.path().join("original");
    std::fs::create_dir(&original_dir).unwrap();
    std::fs::write(
        original_dir.join("hindsight.json"),
        "fn f(x: i32) -> i32 { todo!() }\n",
    )
    .unwrap();
    let baseline = capture(
        &original_dir,
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    let original_subject = baseline.subject_hash;
    let current_subject = evidence.subject.subject_hash;
    std::fs::write(original_dir.join("hindsight.json"), "changed after capture").unwrap();
    let mut evidence = evidence
        .with_baseline(baseline)
        .with_execution(
            crate::CheckEnvironment {
                inputs: vec![],
                docker: "docker".into(),
                image,
                user: "1000:1000".into(),
                candidate_mount: "/candidate".into(),
                timeout_secs: 30,
                build_command: None,
                candidate_command: None,
            },
            crate::ReviewExecution {
                max_calls: 1,
                timeout_secs: 10,
                max_output_bytes: 8192,
                image: None,
                resources: None,
            },
            root.path().join("review"),
            std::time::Duration::from_secs(60),
        )
        .unwrap();
    let response = evidence
        .inspect(
            json!({"source_ids":["file:0","baseline:0"],"requirements":["R1"],
        "python":r#"from pathlib import Path
import json
before=Path('/evidence/baseline-0').read_text()
after=Path('/evidence/file-0').read_text()
assert 'i32' in before and 'u32' in after
for file in ('baseline-0','file-0'):
    try:
        Path('/evidence/'+file).write_text('must remain immutable')
    except OSError as error:
        assert error.errno in (13,30)
    else:
        raise AssertionError('source was writable')
print(json.dumps({'same_signature':before.split('{')[0]==after.split('{')[0]}))
"#}),
            std::time::Duration::from_secs(20),
        )
        .await;
    assert!(response.success, "{response:?}");
    let (text, _) = document(&mut evidence, "computation:0");
    let receipt: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(receipt["schema"], 3);
    assert_eq!(receipt["subject_hash"], json!(current_subject));
    assert_eq!(
        receipt["inputs"][0]["source_subject_hash"],
        json!(current_subject)
    );
    assert_eq!(
        receipt["inputs"][1]["source_subject_hash"],
        json!(original_subject)
    );
    assert_eq!(receipt["execution"]["state"], "completed");
    assert_eq!(receipt["execution"]["shutdown_confirmed"], true);
    assert_eq!(receipt["output_truncated"], false);
    assert_eq!(
        serde_json::from_str::<Value>(receipt["stdout"].as_str().unwrap()).unwrap()["same_signature"],
        false
    );
    println!(
        "{}",
        json!({"original_subject":original_subject,"current_subject":current_subject,
        "receipt_sha256":codex_pro_contract::Digest::of(text.as_bytes()),"execution":receipt["execution"],
        "inputs":receipt["inputs"],"stdout":receipt["stdout"]})
    );
}

#[test]
fn baseline_identity_compares_frozen_entries_not_later_workspace_bytes() {
    let (root, mut evidence) = fixture("original");
    let baseline = evidence.subject.clone();
    evidence = evidence.with_baseline(baseline);
    std::fs::write(
        root.path().join("workspace/hindsight.json"),
        "later mutable work",
    )
    .unwrap();
    let same = read(
        &mut evidence,
        json!({"op":"index","source_id":"","offset":3}),
    );
    assert_eq!(same["entry"]["matches_original_entry"], true);
    evidence.subject = capture(
        &root.path().join("workspace"),
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    let changed = read(
        &mut evidence,
        json!({"op":"index","source_id":"","offset":3}),
    );
    assert_eq!(changed["entry"]["matches_original_entry"], false);
}

fn inspection_fixture(
    root: &std::path::Path,
    evidence: ReviewEvidence,
    image: String,
) -> ReviewEvidence {
    evidence
        .with_execution(
            crate::CheckEnvironment {
                inputs: vec![],
                docker: "docker".into(),
                image,
                user: "1000:1000".into(),
                candidate_mount: "/candidate".into(),
                timeout_secs: 30,
                build_command: None,
                candidate_command: None,
            },
            crate::ReviewExecution {
                max_calls: 1,
                timeout_secs: 20,
                max_output_bytes: 8192,
                image: None,
                resources: None,
            },
            root.join("inspection"),
            std::time::Duration::from_secs(60),
        )
        .unwrap()
}

#[tokio::test]
async fn invalid_source_layout_does_not_reserve_execution() {
    for paths in [
        vec!["../escape.py"],
        vec!["same.py", "same.py"],
        vec!["pkg", "pkg/file.py"],
    ] {
        let (root, mut evidence) = fixture("retained bytes");
        let entry = evidence.subject.manifest.entries[0].clone();
        evidence.subject.manifest.entries = paths
            .iter()
            .map(|path| {
                let mut entry = entry.clone();
                entry.path = (*path).into();
                entry
            })
            .collect();
        let mut evidence = inspection_fixture(root.path(), evidence, "unused-image".into());
        let ids: Vec<_> = (0..paths.len()).map(|i| format!("file:{i}")).collect();
        let response = evidence.inspect(json!({"source_ids":ids,"requirements":["R1"],"python":"print('never dispatched')"}),
            std::time::Duration::from_secs(10)).await;
        assert!(!response.success, "{paths:?}");
        assert!(!root.path().join("inspection/reservation-0.json").exists());
        assert!(!root.path().join("inspection/call-0").exists());
    }
}

#[test]
fn catalog_advertises_complete_named_paths_and_preserves_legacy_paths() {
    let (root, evidence) = fixture("captured");
    let baseline = evidence.subject.clone();
    let mut evidence = inspection_fixture(
        root.path(),
        evidence.with_baseline(baseline),
        "unused".into(),
    );
    let entry = read(
        &mut evidence,
        json!({"op":"index","source_id":"","offset":3}),
    );
    assert_eq!(entry["entry"]["inspection_path"], "/evidence/file-0");
    assert_eq!(
        entry["entry"]["named_inspection_path"],
        "/sources/current/hindsight.json"
    );
    let before = read(
        &mut evidence,
        json!({"op":"index","source_id":"baseline","offset":0}),
    );
    assert_eq!(
        before["entry"]["named_inspection_path"],
        "/sources/baseline/hindsight.json"
    );
    evidence.subject.manifest.entries[0].path =
        format!("{}archive.tar.gz", "directory/".repeat(200));
    let long = read(
        &mut evidence,
        json!({"op":"index","source_id":"","offset":3}),
    );
    assert!(long["entry"]["named_inspection_path"].is_null());
    assert_eq!(long["entry"]["inspection_path"], "/evidence/file-0");
    assert_eq!(
        evidence.capabilities()["inspect_evidence"]["selected_source_manifest"],
        "/sources/manifest.json"
    );
}

#[test]
fn named_receipt_reuse_checks_layout_and_keeps_legacy_semantics() {
    use codex_pro_contract::Digest;
    let (_root, mut evidence) = fixture("artifact");
    let baseline = evidence.subject.clone();
    evidence = evidence.with_baseline(baseline);
    let entry = &evidence.subject.manifest.entries[0];
    let crate::EntryKind::File { digest, len, .. } = entry.kind else {
        panic!("file")
    };
    let inputs: Vec<_> = ["file:0", "baseline:0"].into_iter().map(|id| {
        let paths = crate::inspection::source_paths(id, &entry.path).unwrap();
        json!({"source_id":id,"source_subject_hash":evidence.subject.subject_hash,
            "sha256":digest,"bytes":len,"path":paths.legacy,"named_path":paths.named,"captured_path":entry.path})
    }).collect();
    let binding = Digest::of(b"frozen conditions");
    let valid = json!({"schema":3,"source_layout":crate::inspection::SOURCE_LAYOUT,
        "subject_hash":evidence.subject.subject_hash,"grant_binding":binding,"inputs":inputs});
    let mut legacy = valid.clone();
    legacy["schema"] = json!(2);
    legacy.as_object_mut().unwrap().remove("source_layout");
    for input in legacy["inputs"].as_array_mut().unwrap() {
        input.as_object_mut().unwrap().remove("named_path");
        input.as_object_mut().unwrap().remove("captured_path");
    }
    let mut records = vec![valid.clone(), legacy];
    for (pointer, value) in [
        ("/source_layout", json!("another-layout")),
        (
            "/inputs/0/named_path",
            json!("/sources/current/different.py"),
        ),
        ("/inputs/0/captured_path", json!("different.py")),
        ("/inputs/0/path", json!("/evidence/file-1")),
        (
            "/inputs/1/source_subject_hash",
            json!(Digest::of(b"different baseline")),
        ),
        ("/inputs/1/sha256", json!(Digest::of(b"different bytes"))),
        ("/inputs/1/bytes", json!(1)),
        ("/schema", json!(2)),
    ] {
        let mut changed = valid.clone();
        *changed.pointer_mut(pointer).unwrap() = value;
        assert!(
            evidence.validate_computation_sources(&changed).is_err(),
            "{pointer}"
        );
        records.push(changed);
    }
    let handles: Vec<_> = records
        .iter()
        .map(|record| evidence.store.put(record.to_string().as_bytes()).unwrap())
        .collect();
    let expected: std::collections::BTreeSet<_> = handles[..2]
        .iter()
        .map(|hash| format!("receipt:{hash}"))
        .collect();
    evidence = evidence.with_retained_computations(Ok(handles), Ok(binding));
    assert_eq!(evidence.retained_computations, expected);
}

#[tokio::test]
#[ignore = "requires an explicitly selected local Python and C compiler image"]
async fn named_sources_support_standard_loaders_and_relative_includes() {
    let image = std::env::var("PRO_CONTRACT_TEST_IMAGE").expect("select a local test image");
    let (root, mut evidence) = fixture("not selected");
    let workspace = root.path().join("workspace");
    std::fs::create_dir(workspace.join("pkg")).unwrap();
    std::fs::write(workspace.join("pkg/module.py"), "value = 42\n").unwrap();
    std::fs::write(workspace.join("pkg/value.h"), "#define VALUE 42\n").unwrap();
    std::fs::write(workspace.join("pkg/main.c"), "#include \"value.h\"\n#include <stdio.h>\nint main(void) { printf(\"%d\\n\", VALUE); return 0; }\n").unwrap();
    let original = root.path().join("original");
    std::fs::create_dir_all(original.join("pkg")).unwrap();
    std::fs::write(original.join("pkg/module.py"), "value = 7\n").unwrap();
    let baseline = capture(&original, &CapturePolicy::standard(vec![]), &evidence.store).unwrap();
    evidence.subject = capture(
        &workspace,
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    let source_ids: Vec<_> = [("file", &evidence.subject), ("baseline", &baseline)]
        .into_iter()
        .flat_map(|(kind, subject)| {
            subject
                .manifest
                .entries
                .iter()
                .enumerate()
                .filter_map(move |(i, entry)| {
                    (entry.path.starts_with("pkg/")
                        && matches!(entry.kind, crate::EntryKind::File { .. }))
                    .then(|| format!("{kind}:{i}"))
                })
        })
        .collect();
    std::fs::write(
        workspace.join("pkg/module.py"),
        "raise RuntimeError('live workspace')\n",
    )
    .unwrap();
    evidence = inspection_fixture(root.path(), evidence.with_baseline(baseline), image);
    let response = evidence.inspect(json!({"source_ids":source_ids,"requirements":["R1"],"python":r#"
import importlib.util, json, subprocess
from pathlib import Path
manifest = json.loads(Path('/sources/manifest.json').read_text())
assert manifest['source_layout'] == 'selected_relative_files_v1'
inputs = manifest['inputs']
assert len(inputs) == 4
assert set(p.name for p in Path('/evidence').iterdir()) == {Path(i['path']).name for i in inputs}
for i in inputs:
    assert Path(i['path']).read_bytes() == Path(i['named_path']).read_bytes()
    for path in (i['path'], i['named_path']):
        try: Path(path).write_bytes(b'changed')
        except OSError as error: assert error.errno in (13, 30)
        else: raise AssertionError('writable source')
current = next(i for i in inputs if i['named_path'] == '/sources/current/pkg/module.py')
assert importlib.util.spec_from_file_location('old', current['path']) is None
for name, value in [('current', 42), ('baseline', 7)]:
    spec = importlib.util.spec_from_file_location(name, f'/sources/{name}/pkg/module.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.value == value
assert not Path('/sources/current/hindsight.json').exists()
assert not Path('/sources/baseline/pkg/value.h').exists()
c = next(i for i in inputs if i['named_path'].endswith('/main.c'))
old = subprocess.run(['cc', c['path'], '-o', '/tmp/old'], capture_output=True)
assert old.returncode != 0
subprocess.run(['cc', c['named_path'], '-o', '/tmp/check'], check=True)
assert Path('/tmp/check').read_bytes()[:4] == b'\x7fELF'
print('standard Python loader and C extension/include succeed; exact frozen bytes and read-only isolation')
"#}), std::time::Duration::from_secs(30)).await;
    assert!(response.success, "{response:?}");
    let (text, _) = document(&mut evidence, "computation:0");
    let receipt: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(receipt["schema"], 3);
    assert_eq!(receipt["execution"]["state"], "completed", "{receipt}");
    assert_eq!(receipt["execution"]["shutdown_confirmed"], true);
    evidence.validate_computation_sources(&receipt).unwrap();
    println!("{text}");
}

#[test]
#[ignore = "requires an explicitly selected host history projection fixture"]
fn native_history_fixture_is_available_through_real_paged_reader() {
    let selected = std::path::PathBuf::from(
        std::env::var("PRO_CONTRACT_HISTORY_FIXTURE").expect("select fixture"),
    );
    let expected: Value =
        serde_json::from_slice(&std::fs::read(selected.join("EXPECTED.json")).unwrap()).unwrap();
    let provider: Value =
        serde_json::from_slice(&std::fs::read(selected.join("PROVIDER.json")).unwrap()).unwrap();
    let (root, mut evidence) = fixture("not used as execution evidence");
    evidence
        .terms
        .process_constraints
        .push(crate::ProcessConstraint {
            text: "The selected native execution observations must be retrievable.".into(),
            source_quote: "The selected native execution observations must be retrievable.".into(),
        });
    evidence.subject = capture(
        &selected.join("projection"),
        &CapturePolicy::standard(vec![]),
        &evidence.store,
    )
    .unwrap();
    evidence = ReviewEvidence::new(
        evidence.subject,
        evidence.store,
        &evidence.terms,
        "retained history fixture".into(),
    )
    .unwrap();
    let subject = evidence.subject.subject_hash;
    let context = json!({"kind":"host_observations_v1", "subject_hash":subject,
        "terms_hash":crate::digest_of("terms", &evidence.terms), "capture_projection":{
            "subject_hash":subject, "producer":codex_pro_contract::Digest::of(&std::fs::read(selected.join("PROVIDER.json")).unwrap()),
            "projection":provider["projection"]}});
    evidence = evidence.with_host_observations(context).unwrap();
    for source in ["human_request", "contract_terms"] {
        document(&mut evidence, source);
    }
    let mut catalog = std::collections::BTreeMap::new();
    let mut cursor = 0;
    loop {
        let page = read(
            &mut evidence,
            json!({"op":"list","source_id":"","offset":cursor}),
        );
        for entry in page["entries"].as_array().unwrap() {
            if let (Some(path), Some(id)) = (entry["path"].as_str(), entry["source_id"].as_str()) {
                catalog.insert(path.to_owned(), (id.to_owned(), entry.clone()));
            }
        }
        if page["eof"] == true {
            break;
        }
        cursor = page["next_offset"].as_u64().unwrap();
    }
    let index_id = &catalog["EXECUTION_HISTORY/index.json"].0;
    let (text, index_citation) = document(&mut evidence, index_id);
    let index: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(
        index["events"].as_array().unwrap().len(),
        expected["event_sha256"].as_array().unwrap().len()
    );
    assert!(
        index["scope"]
            .as_str()
            .unwrap()
            .contains("not distinct-operation counts")
    );
    assert!(
        evidence
            .resolve_citation("process:0", index_citation.as_str().unwrap())
            .is_err()
    );
    let mut chunks = std::collections::BTreeMap::new();
    for chunk in index["chunks"].as_array().unwrap() {
        let path = chunk["path"].as_str().unwrap();
        let (id, entry) = &catalog[path];
        assert_eq!(entry["kind"], "host_observation");
        assert_eq!(entry["origin"]["kind"], "execution_observation");
        let (text, citation) = document(&mut evidence, id);
        assert_eq!(text.len() as u64, chunk["bytes"].as_u64().unwrap());
        assert_eq!(
            json!(codex_pro_contract::Digest::of(text.as_bytes())),
            chunk["sha256"]
        );
        evidence
            .resolve_citation("process:0", citation.as_str().unwrap())
            .unwrap();
        chunks.insert(path.to_owned(), text.into_bytes());
    }
    let mut actual = Vec::new();
    for event in index["events"].as_array().unwrap() {
        let data = &chunks[event["chunk"].as_str().unwrap()];
        let offset = event["offset"].as_u64().unwrap() as usize;
        let length = event["bytes"].as_u64().unwrap() as usize;
        let raw = &data[offset..offset + length];
        let sha = json!(codex_pro_contract::Digest::of(raw));
        assert_eq!(sha, event["sha256"]);
        assert_eq!(sha, event["origin"]["sha256"]);
        let record: Value = serde_json::from_slice(raw).unwrap();
        assert_eq!(record["payload"]["item"]["id"], event["item_id"]);
        actual.push(sha);
    }
    assert_eq!(json!(actual), expected["event_sha256"]);
    assert!(actual.contains(&expected["early_sha256"]));
    assert!(
        !expected["old_tail_sha256"]
            .as_array()
            .unwrap()
            .contains(&expected["early_sha256"])
    );
    println!(
        "{}",
        json!({"actual_records":actual.len(),"chunks":chunks.len(),"reader_calls":evidence.calls,
        "source_subject":subject,"scope":index["scope"],"early_event_sha256":expected["early_sha256"],
        "original_bytes_reconstructed":true,"metadata_cannot_attest_execution":true})
    );
    drop(root);
}
