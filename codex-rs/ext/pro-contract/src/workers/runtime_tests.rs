use super::worker_tools;
use codex_extension_api::ToolName;
use pretty_assertions::assert_eq;

#[test]
fn archive_worker_instructions_match_installed_read_capability() {
    let empty = worker_tools(None, false);
    assert!(empty.allowed.is_empty());
    assert!(empty.dynamic.is_empty());
    assert!(empty.instructions.contains("You have no tools"));

    let archive = worker_tools(None, true);
    assert_eq!(
        archive.allowed,
        ["contract_read_evidence", "exec", "wait"]
            .map(ToolName::plain)
            .to_vec()
    );
    assert!(
        archive.dynamic.is_empty(),
        "archive uses its native contributor"
    );
    assert!(!archive.instructions.contains("You have no tools"));
    assert!(archive.instructions.contains("contract_read_evidence"));
    assert!(archive.instructions.contains("no host shell"));
    assert!(archive.instructions.contains("image-rendering tool"));
}

#[test]
fn reviewer_tool_registration_uses_opened_grant_not_configuration_intent() {
    let root = tempfile::tempdir().unwrap();
    let store = crate::BlobStore::open(&root.path().join("blobs")).unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let subject =
        crate::capture(&workspace, &crate::CapturePolicy::standard(vec![]), &store).unwrap();
    let terms = crate::Terms {
        intake_text: "Inspect the artifact".into(),
        requirements: vec![],
        out_of_scope: vec![],
        process_constraints: vec![],
        context: vec![],
    };
    let evidence = crate::workers::review_evidence::ReviewEvidence::new(
        subject,
        store,
        &terms,
        "No checks".into(),
    )
    .unwrap();
    let limits = crate::ReviewExecution {
        max_calls: 1,
        timeout_secs: 5,
        max_output_bytes: 4096,
        image: None,
        resources: None,
    };
    let before = worker_tools(Some(&evidence), false);
    assert_eq!(
        before.allowed,
        ["read_evidence", "exec", "wait"]
            .map(ToolName::plain)
            .to_vec()
    );
    assert_eq!(before.dynamic.len(), 1);
    assert!(evidence.capabilities()["inspect_evidence"].is_null());
    let evidence = evidence
        .with_execution(
            crate::CheckEnvironment {
                inputs: vec![],
                docker: "/unavailable-test-docker".into(),
                image: "test-image".into(),
                user: "1000:1000".into(),
                candidate_mount: "/candidate".into(),
                timeout_secs: 5,
                build_command: None,
                candidate_command: None,
            },
            limits,
            root.path().join("grant"),
            std::time::Duration::ZERO,
        )
        .unwrap();
    let after = worker_tools(Some(&evidence), false);
    assert_eq!(
        after.allowed,
        ["read_evidence", "inspect_evidence", "exec", "wait"]
            .map(ToolName::plain)
            .to_vec()
    );
    assert_eq!(after.dynamic.len(), 2);
    assert!(
        after
            .instructions
            .contains("Tool installation does not attest execution success")
    );
    assert_eq!(
        evidence.capabilities()["inspect_evidence"]["tool_installed"],
        true
    );
    // Even an expired original grant keeps the tool's interface; admission remains in inspect.
    assert!(
        evidence.capabilities()["inspect_evidence"]["admission"]
            .as_str()
            .unwrap()
            .contains("remaining time")
    );
    assert!(!root.path().join("grant/reservation-0.json").exists());
}
