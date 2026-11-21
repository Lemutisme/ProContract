use super::command;
use super::coordinate;
use super::id;
use super::issue;
use super::open;
use codex_pro_contract::Command;
use codex_pro_contract::Contract;
use codex_pro_contract::DecisionProvenance;
use codex_pro_contract::Digest;
use codex_pro_contract::Role;
use codex_pro_contract::transition;
use pretty_assertions::assert_eq;
use serde_json::json;

use crate::AdoptionRequest;
use crate::ExperimentEvent;
use crate::ExperimentKind;
use crate::Identities;
use crate::Ledger;
use crate::LedgerError;
use crate::digest_of;

async fn supported_adoption(ledger: &Ledger) -> (Contract, AdoptionRequest) {
    let issued = ledger.apply(&issue(), "issue").await.unwrap();
    let proposed = ledger
        .apply(
            &command(
                issued.version,
                Role::Executor,
                Command::Propose {
                    subject_hash: Digest::of(b"candidate"),
                },
            ),
            "propose",
        )
        .await
        .unwrap();
    let coordinate = coordinate(&proposed);
    let supported = ledger
        .apply(
            &command(
                proposed.version,
                Role::Verifier,
                Command::Support {
                    certificate: Digest::of(b"certificate"),
                    coordinate: coordinate.clone(),
                },
            ),
            "support",
        )
        .await
        .unwrap();
    let request = AdoptionRequest {
        command: command(
            supported.version,
            Role::Settler,
            Command::Discharge {
                attestation: Digest::of(b"accept"),
                coordinate,
                decision: DecisionProvenance::Explicit,
            },
        ),
        expected_incumbent_seq: None,
        identities: Identities::default(),
        supports: Vec::new(),
    };
    (supported, request)
}

#[tokio::test]
async fn adoption_and_retries_bind_the_entire_request_and_preserve_both_chains() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let (_, request) = supported_adoption(&ledger).await;
    let adopted = ledger.adopt(&request, "adopt").await.unwrap();
    let experiments = ledger.experiments().await.unwrap();
    assert_eq!(
        experiments
            .iter()
            .map(|record| &record.event)
            .collect::<Vec<_>>(),
        vec![&ExperimentEvent {
            kind: ExperimentKind::Selection,
            identities: request.identities.clone(),
            body: json!({"role": "incumbent", "version": Digest::of(b"candidate").to_string(), "adoption": id()}),
        }]
    );
    let events = ledger.events().await.unwrap();
    let retried = crate::cli::adopt_command(
        dir.path(),
        "adopt",
        &serde_json::to_string(&request).unwrap(),
    )
    .await
    .unwrap();
    assert_eq!(retried, adopted);
    let mut changes = [request.clone(), request.clone(), request.clone()];
    changes[0].expected_incumbent_seq = Some(experiments[0].seq);
    changes[1].identities.model = Some("different".to_string());
    changes[2].command.provenance = codex_pro_contract::Provenance::Automation;
    for changed in changes {
        assert!(matches!(
            ledger.adopt(&changed, "adopt").await,
            Err(LedgerError::IdempotencyConflict)
        ));
    }
    assert_eq!(ledger.experiments().await.unwrap(), experiments);
    assert_eq!(ledger.events().await.unwrap(), events);
    ledger.verify_experiment_chain().await.unwrap();
    let mut state = None;
    let mut previous = Digest::from_bytes([0; 32]);
    for event in events {
        state = Some(transition(state.as_ref(), &event.command).unwrap());
        let state_hash = digest_of("contract_state", state.as_ref().unwrap());
        assert_eq!(
            (event.prev_hash, event.state_hash, event.hash),
            (
                previous,
                state_hash,
                digest_of("event", &(previous, &event.command, state_hash))
            )
        );
        previous = event.hash;
    }
    assert_eq!(state, Some(adopted));
}

#[tokio::test]
async fn a_stale_incumbent_fence_changes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let (supported, mut request) = supported_adoption(&ledger).await;
    // The incumbent changes by an adoption. A selection another writer appends, of any role,
    // is not a selection of an incumbent and does not move the fence.
    let previous = candidate_of(&ledger, "previous", b"previous", None, Vec::new()).await;
    ledger.adopt(&previous, "adopt-previous").await.unwrap();
    let incumbent = latest_selection(&ledger).await;
    for role in ["incumbent", "research_parent"] {
        ledger
            .append_experiment(&ExperimentEvent {
                kind: ExperimentKind::Selection,
                identities: Identities::default(),
                body: json!({"role": role, "version": "other"}),
            })
            .await
            .unwrap();
    }
    let events = ledger.events().await.unwrap();
    let experiments = ledger.experiments().await.unwrap();
    assert!(matches!(ledger.adopt(&request, "adopt").await,
        Err(LedgerError::IncumbentChanged { expected: None, actual: Some(seq) }) if seq == incumbent));
    assert_eq!(ledger.contract(&id()).await.unwrap(), Some(supported));
    assert_eq!(ledger.events().await.unwrap(), events);
    assert_eq!(ledger.experiments().await.unwrap(), experiments);
    request.expected_incumbent_seq = Some(incumbent);
    ledger.adopt(&request, "adopt").await.unwrap();
    ledger.verify_experiment_chain().await.unwrap();
}

#[tokio::test]
async fn selection_failure_rolls_back_discharge_and_idempotency() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let (supported, request) = supported_adoption(&ledger).await;
    let events = ledger.events().await.unwrap();
    let mut invalid = request.clone();
    invalid.command.command = Command::Release {
        attestation: Digest::of(b"release"),
    };
    assert!(matches!(
        ledger.adopt(&invalid, "adopt").await,
        Err(LedgerError::InvalidEvent(_))
    ));
    invalid = request.clone();
    invalid.command.role = Role::Executor;
    assert!(matches!(
        ledger.adopt(&invalid, "adopt").await,
        Err(LedgerError::Rejected(_))
    ));
    sqlx::query(
        "CREATE TRIGGER reject_selection BEFORE INSERT ON experiment_events
        BEGIN SELECT RAISE(ABORT, 'simulated failure'); END",
    )
    .execute(&ledger.pool)
    .await
    .unwrap();
    assert!(matches!(
        ledger.adopt(&request, "adopt").await,
        Err(LedgerError::Storage(_))
    ));
    assert_eq!(ledger.contract(&id()).await.unwrap(), Some(supported));
    assert_eq!(ledger.events().await.unwrap(), events);
    assert_eq!(ledger.experiments().await.unwrap(), vec![]);
    sqlx::query("DROP TRIGGER reject_selection")
        .execute(&ledger.pool)
        .await
        .unwrap();
    ledger.adopt(&request, "adopt").await.unwrap();
}

fn on(
    name: &str,
    mut command: codex_pro_contract::AuthenticatedCommand,
) -> codex_pro_contract::AuthenticatedCommand {
    command.contract_id = codex_pro_contract::ContractId(name.to_string());
    command
}

/// Supports the current candidate of `name` and returns the request that would adopt it.
async fn adoption_of(
    ledger: &Ledger,
    name: &str,
    expected_incumbent_seq: Option<i64>,
    supports: Vec<i64>,
) -> AdoptionRequest {
    let contract = ledger
        .contract(&codex_pro_contract::ContractId(name.to_string()))
        .await
        .unwrap()
        .unwrap();
    let coordinate = coordinate(&contract);
    let supported = ledger
        .apply(
            &on(
                name,
                command(
                    contract.version,
                    Role::Verifier,
                    Command::Support {
                        certificate: Digest::of(b"certificate"),
                        coordinate: coordinate.clone(),
                    },
                ),
            ),
            &format!("support-{name}-{}", contract.version),
        )
        .await
        .unwrap();
    AdoptionRequest {
        command: on(
            name,
            command(
                supported.version,
                Role::Settler,
                Command::Discharge {
                    attestation: Digest::of(b"accept"),
                    coordinate,
                    decision: DecisionProvenance::Explicit,
                },
            ),
        ),
        expected_incumbent_seq,
        identities: Identities::default(),
        supports,
    }
}

/// Issues `name` with a candidate and returns the request that would adopt it.
async fn candidate_of(
    ledger: &Ledger,
    name: &str,
    subject: &[u8],
    expected_incumbent_seq: Option<i64>,
    supports: Vec<i64>,
) -> AdoptionRequest {
    let issued = ledger
        .apply(&on(name, issue()), &format!("issue-{name}"))
        .await
        .unwrap();
    ledger
        .apply(
            &on(
                name,
                command(
                    issued.version,
                    Role::Executor,
                    Command::Propose {
                        subject_hash: Digest::of(subject),
                    },
                ),
            ),
            &format!("propose-{name}"),
        )
        .await
        .unwrap();
    adoption_of(ledger, name, expected_incumbent_seq, supports).await
}

async fn withdraw(ledger: &Ledger, name: &str) -> Contract {
    let contract = ledger
        .contract(&codex_pro_contract::ContractId(name.to_string()))
        .await
        .unwrap()
        .unwrap();
    ledger
        .apply(
            &on(
                name,
                command(
                    contract.version,
                    Role::Verifier,
                    Command::Withdraw {
                        target: codex_pro_contract::Target::Settlement {
                            coordinate: contract.settlement.unwrap().coordinate,
                        },
                        cause: Digest::of(b"evidence failed"),
                    },
                ),
            ),
            &format!("withdraw-{name}-{}", contract.version),
        )
        .await
        .unwrap()
}

async fn latest_selection(ledger: &Ledger) -> i64 {
    ledger.experiments().await.unwrap().last().unwrap().seq
}

#[tokio::test]
async fn an_adoption_commits_only_while_its_supports_are_live() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let parent = candidate_of(&ledger, "parent", b"v1", None, Vec::new()).await;
    assert_eq!(serde_json::to_value(&parent).unwrap().get("supports"), None);
    ledger.adopt(&parent, "adopt-parent").await.unwrap();
    let first = latest_selection(&ledger).await;
    assert!(ledger.live(first).await.unwrap());

    // The child was judged against that acceptance; the parent is withdrawn before it commits.
    let child = candidate_of(&ledger, "child", b"v2", Some(first), vec![first]).await;
    withdraw(&ledger, "parent").await;
    let before = (
        ledger.events().await.unwrap(),
        ledger.experiments().await.unwrap(),
    );
    assert!(matches!(
        ledger.adopt(&child, "adopt-child").await,
        Err(LedgerError::SupportNotLive { selection }) if selection == first
    ));
    assert_eq!(
        (
            ledger.events().await.unwrap(),
            ledger.experiments().await.unwrap()
        ),
        before
    );

    // The same content accepted again is another acceptance and does not revive the old edge.
    let again = adoption_of(&ledger, "parent", Some(first), Vec::new()).await;
    ledger.adopt(&again, "adopt-parent-again").await.unwrap();
    let second = latest_selection(&ledger).await;
    assert!(!ledger.live(first).await.unwrap());
    assert!(ledger.live(second).await.unwrap());
    let mut stale = child.clone();
    stale.expected_incumbent_seq = Some(second);
    assert!(matches!(
        ledger.adopt(&stale, "adopt-child-stale").await,
        Err(LedgerError::SupportNotLive { selection }) if selection == first
    ));

    let mut rebuilt = child;
    rebuilt.expected_incumbent_seq = Some(second);
    rebuilt.supports = vec![second];
    let adopted = ledger.adopt(&rebuilt, "adopt-child-rebuilt").await.unwrap();
    let third = latest_selection(&ledger).await;
    let recorded = ledger
        .experiments()
        .await
        .unwrap()
        .pop()
        .unwrap()
        .event
        .body;
    assert_eq!(
        recorded,
        json!({
            "role": "incumbent",
            "version": Digest::of(b"v2").to_string(),
            "adoption": "child",
            "generation": adopted.settlement.unwrap().coordinate.generation,
            "supports": [second],
        })
    );
    assert!(ledger.live(third).await.unwrap());

    // Liveness follows the edges: withdrawing the parent ends the child's without touching it.
    withdraw(&ledger, "parent").await;
    assert!(!ledger.live(third).await.unwrap());
    ledger.verify_experiment_chain().await.unwrap();
}

#[tokio::test]
async fn selecting_again_is_fenced_and_names_a_live_acceptance() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    ledger.adopt(&root, "adopt-root").await.unwrap();
    let first = latest_selection(&ledger).await;
    let child = candidate_of(&ledger, "child", b"v1", Some(first), vec![first]).await;
    ledger.adopt(&child, "adopt-child").await.unwrap();
    let second = latest_selection(&ledger).await;
    withdraw(&ledger, "child").await;
    assert_eq!(
        (
            ledger.live(first).await.unwrap(),
            ledger.live(second).await.unwrap()
        ),
        (true, false)
    );

    let request = crate::SelectionRequest {
        acceptance: first,
        expected_incumbent_seq: Some(second),
        identities: Identities::default(),
        decision: json!({"type": "explicit", "receipt": "the principal reselected v0"}),
    };
    let mut stale = request.clone();
    stale.expected_incumbent_seq = Some(first);
    assert!(matches!(
        ledger.select(&stale, "select-stale").await,
        Err(LedgerError::IncumbentChanged { .. })
    ));
    let mut dead = request.clone();
    dead.acceptance = second;
    assert!(matches!(
        ledger.select(&dead, "select-dead").await,
        Err(LedgerError::SupportNotLive { selection }) if selection == second
    ));
    let third = ledger.select(&request, "select").await.unwrap();
    assert_eq!(ledger.select(&request, "select").await.unwrap(), third);
    assert!(matches!(
        ledger.select(&dead, "select").await,
        Err(LedgerError::IdempotencyConflict)
    ));
    let recorded = ledger.experiments().await.unwrap().pop().unwrap();
    assert_eq!(
        (recorded.seq, recorded.event.body),
        (
            third,
            json!({
                "role": "incumbent",
                "version": Digest::of(b"v0").to_string(),
                "adoption": "root",
                "restores": first,
                "decision": {"type": "explicit", "receipt": "the principal reselected v0"},
            })
        )
    );
    // A selection that restores is not an acceptance of its own.
    assert!(!ledger.live(third).await.unwrap());
    ledger.verify_experiment_chain().await.unwrap();
}

#[tokio::test]
async fn an_appended_selection_is_not_an_acceptance() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    ledger.adopt(&root, "adopt-root").await.unwrap();
    let first = latest_selection(&ledger).await;
    let child = candidate_of(&ledger, "child", b"v1", Some(first), vec![first]).await;
    ledger.adopt(&child, "adopt-child").await.unwrap();
    let second = latest_selection(&ledger).await;
    withdraw(&ledger, "root").await;
    assert_eq!(
        (
            ledger.live(first).await.unwrap(),
            ledger.live(second).await.unwrap()
        ),
        (false, false)
    );

    // The child's Contract is still settled. Another writer appends a selection that looks
    // like its adoption; the journal takes it, and it confers nothing.
    let forged = ledger
        .append_experiment(&ExperimentEvent {
            kind: ExperimentKind::Selection,
            identities: Identities::default(),
            body: json!({"role": "incumbent", "version": Digest::of(b"v1").to_string(), "adoption": "child"}),
        })
        .await
        .unwrap()
        .seq;
    assert!(!ledger.live(forged).await.unwrap());
    let grandchild = candidate_of(&ledger, "grandchild", b"v2", Some(second), vec![forged]).await;
    assert!(matches!(
        ledger.adopt(&grandchild, "adopt-grandchild").await,
        Err(LedgerError::SupportNotLive { selection }) if selection == forged
    ));
    let restoring = crate::SelectionRequest {
        acceptance: forged,
        expected_incumbent_seq: Some(second),
        identities: Identities::default(),
        decision: json!({"type": "explicit", "receipt": "r"}),
    };
    assert!(matches!(
        ledger.select(&restoring, "select-forged").await,
        Err(LedgerError::SupportNotLive { selection }) if selection == forged
    ));
}

#[tokio::test]
async fn settling_again_outside_adoption_revives_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    ledger.adopt(&root, "adopt-root").await.unwrap();
    let first = latest_selection(&ledger).await;
    let child = candidate_of(&ledger, "child", b"v1", Some(first), vec![first]).await;
    ledger.adopt(&child, "adopt-child").await.unwrap();
    let second = latest_selection(&ledger).await;
    withdraw(&ledger, "root").await;

    // The same content is supported and discharged again through the plain command path: a
    // valid settlement of the Contract, with no adoption and so no acceptance.
    let again = adoption_of(&ledger, "root", Some(second), Vec::new()).await;
    let settled = ledger.apply(&again.command, "raw-discharge").await.unwrap();
    assert_eq!(settled.standing, codex_pro_contract::Standing::Discharged);
    assert_eq!(latest_selection(&ledger).await, second);
    assert_eq!(
        (
            ledger.live(first).await.unwrap(),
            ledger.live(second).await.unwrap()
        ),
        (false, false)
    );
}

#[tokio::test]
async fn appended_selections_neither_select_nor_move_the_fence() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    assert_eq!(ledger.incumbent().await.unwrap(), None);
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    ledger.adopt(&root, "adopt-root").await.unwrap();
    let first = latest_selection(&ledger).await;
    let other = candidate_of(&ledger, "other", b"v1", Some(first), vec![first]).await;

    // Another writer appends a selection shaped like a restore of the live root that names the
    // unadopted v1, and one shaped like a fresh choice of the root.
    for body in [
        json!({"role": "incumbent", "version": Digest::of(b"v1").to_string(), "adoption": "other", "restores": first}),
        json!({"role": "incumbent", "version": Digest::of(b"v0").to_string(), "adoption": "root"}),
    ] {
        ledger
            .append_experiment(&ExperimentEvent {
                kind: ExperimentKind::Selection,
                identities: Identities::default(),
                body,
            })
            .await
            .unwrap();
    }
    let appended = latest_selection(&ledger).await;
    let current = ledger.incumbent().await.unwrap().unwrap();
    assert_eq!(
        (
            current.selection,
            current.acceptance.selection,
            current.acceptance.live
        ),
        (first, first, true)
    );
    assert_eq!(
        current.acceptance.subject_hash,
        Digest::of(b"v0").to_string()
    );
    let listed = ledger.acceptances().await.unwrap();
    assert_eq!(listed, vec![current.acceptance.clone()]);

    // The fence is still the store's own selection: the appended ones are not it.
    let mut moved = other.clone();
    moved.expected_incumbent_seq = Some(appended);
    assert!(matches!(
        ledger.adopt(&moved, "adopt-moved").await,
        Err(LedgerError::IncumbentChanged { .. })
    ));
    ledger.adopt(&other, "adopt-other").await.unwrap();
    let adopted = ledger.incumbent().await.unwrap().unwrap();
    assert_eq!(
        (
            adopted.selection,
            adopted.acceptance.supports.clone(),
            adopted.acceptance.live
        ),
        (latest_selection(&ledger).await, vec![first], true)
    );
}

/// What the earlier binary's adoption committed: the settlement under an `adoption`-domain
/// idempotency row and the journal selection, with no acceptance or incumbent row.
async fn earlier_adoption(ledger: &Ledger, request: &AdoptionRequest, key: &str) -> i64 {
    let mut tx = ledger.pool.begin_with("BEGIN IMMEDIATE").await.unwrap();
    let hash = digest_of("adoption", request).to_string();
    let next = Ledger::apply_in_transaction(&mut tx, &request.command, key, &hash)
        .await
        .unwrap();
    let record = Ledger::append_experiment_in_transaction(
        &mut tx,
        &ExperimentEvent {
            kind: ExperimentKind::Selection,
            identities: request.identities.clone(),
            body: json!({
                "role": "incumbent",
                "version": next.settlement.unwrap().coordinate.subject_hash.to_string(),
                "adoption": request.command.contract_id,
            }),
        },
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    record.seq
}

async fn state_of(ledger: &Ledger) -> crate::MigrationState {
    let events = ledger.events().await.unwrap();
    let journal = ledger.experiments().await.unwrap();
    crate::MigrationState {
        kernel_events: events.len() as i64,
        kernel_head: events.last().unwrap().hash.to_string(),
        journal_events: journal.len() as i64,
        journal_head: journal.last().unwrap().hash.to_string(),
    }
}

#[tokio::test]
async fn a_migration_registers_only_what_the_store_itself_can_verify() {
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    let first = earlier_adoption(&ledger, &root, "adoption-root").await;
    // A second Contract settled through the plain command path, with an appended selection.
    let plain = candidate_of(&ledger, "plain", b"v1", Some(first), Vec::new()).await;
    ledger
        .apply(&plain.command, "plain-discharge")
        .await
        .unwrap();
    let forged = ledger
        .append_experiment(&ExperimentEvent {
            kind: ExperimentKind::Selection,
            identities: Identities::default(),
            body: json!({"role": "incumbent", "version": Digest::of(b"v1").to_string(), "adoption": "plain"}),
        })
        .await
        .unwrap()
        .seq;
    assert_eq!(ledger.incumbent().await.unwrap(), None);
    assert!(ledger.acceptances().await.unwrap().is_empty());
    let events = ledger.events().await.unwrap();
    let settled = |name: &str| {
        events
            .iter()
            .find(|event| {
                event.contract_id.0 == name
                    && matches!(event.command.command, Command::Discharge { .. })
            })
            .unwrap()
            .seq
    };
    let nominate = |selection, event| crate::MigratedAcceptance { selection, event };
    let request = crate::MigrationRequest {
        manifest: "manifest-digest".to_string(),
        state: state_of(&ledger).await,
        acceptances: vec![nominate(first, settled("root"))],
        selection_to_activate: None,
        authorization: "the principal authorized this migration".to_string(),
        identities: Identities::default(),
    };
    let before = (
        ledger.events().await.unwrap(),
        ledger.experiments().await.unwrap(),
    );
    // Refused, with nothing changed.
    // The plain settlement with its appended selection.
    let mut wrong = request.clone();
    wrong.acceptances.push(nominate(forged, settled("plain")));
    assert!(matches!(
        ledger.migrate(&wrong, "m-plain").await,
        Err(LedgerError::NotAnAdoption { selection, .. }) if selection == forged
    ));
    // The real adoption's settling event offered again under the appended entry: the fence at
    // that position is the first selection, and no request was retained for it there.
    wrong = request.clone();
    wrong.acceptances = vec![nominate(forged, settled("root"))];
    assert!(matches!(
        ledger.migrate(&wrong, "m-moved").await,
        Err(LedgerError::NotAnAdoption { .. })
    ));
    // Activating the first adoption when the store's latest entry is another: not a migration.
    wrong = request.clone();
    wrong.selection_to_activate = Some(first);
    assert!(matches!(
        ledger.migrate(&wrong, "m-choose").await,
        Err(LedgerError::NotAnAdoption { selection, .. }) if selection == first
    ));
    wrong = request.clone();
    wrong.state.journal_events += 1;
    assert!(matches!(
        ledger.migrate(&wrong, "m-state").await,
        Err(LedgerError::MigrationStateChanged)
    ));
    wrong = request.clone();
    wrong.authorization = " ".to_string();
    assert!(matches!(
        ledger.migrate(&wrong, "m-authority").await,
        Err(LedgerError::InvalidEvent(_))
    ));
    assert_eq!(
        (
            ledger.events().await.unwrap(),
            ledger.experiments().await.unwrap()
        ),
        before
    );
    assert!(ledger.acceptances().await.unwrap().is_empty());

    let migrated = ledger.migrate(&request, "migration").await.unwrap();
    assert_eq!(
        (migrated.registered.clone(), migrated.activated),
        (vec![first], None)
    );
    assert_eq!(
        ledger.migrate(&request, "migration").await.unwrap(),
        migrated
    );
    // The first adoption is registered and live; nothing is selected, because the store's
    // latest entry was not a verifiable adoption and a migration does not choose in its place.
    assert_eq!(ledger.incumbent().await.unwrap(), None);
    let listed = ledger.acceptances().await.unwrap();
    assert_eq!(
        listed
            .iter()
            .map(|row| (row.selection, row.live, row.supports.clone()))
            .collect::<Vec<_>>(),
        vec![(first, true, Vec::new())]
    );
    assert!(!ledger.live(forged).await.unwrap());
    // Once it records acceptances, a store is not migrated again.
    let mut again = request.clone();
    again.state = state_of(&ledger).await;
    assert!(matches!(
        ledger.migrate(&again, "migration-again").await,
        Err(LedgerError::InvalidEvent(_))
    ));
    ledger.verify_experiment_chain().await.unwrap();
}

#[tokio::test]
async fn a_migration_restores_the_current_selection_and_registers_only_the_first_adoption() {
    // A store whose only adoption is its first: registered and restored as current.
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    let first = earlier_adoption(&ledger, &root, "adoption-root").await;
    let settling = ledger.events().await.unwrap().last().unwrap().seq;
    let request = crate::MigrationRequest {
        manifest: "manifest-digest".to_string(),
        state: state_of(&ledger).await,
        acceptances: vec![crate::MigratedAcceptance {
            selection: first,
            event: settling,
        }],
        selection_to_activate: Some(first),
        authorization: "authorized".to_string(),
        identities: Identities::default(),
    };
    let migrated = ledger.migrate(&request, "migration").await.unwrap();
    assert_eq!(
        (migrated.registered, migrated.activated),
        (vec![first], Some(first))
    );
    let current = ledger.incumbent().await.unwrap().unwrap();
    assert_eq!((current.selection, current.acceptance.live), (first, true));

    // A later real adoption of the earlier protocol: verifiable, but what it was built on was
    // never recorded, so it is refused and nothing of the request is registered.
    let dir = tempfile::tempdir().unwrap();
    let ledger = open(dir.path()).await;
    let root = candidate_of(&ledger, "root", b"v0", None, Vec::new()).await;
    let first = earlier_adoption(&ledger, &root, "adoption-root").await;
    let child = candidate_of(&ledger, "child", b"v1", Some(first), Vec::new()).await;
    let second = earlier_adoption(&ledger, &child, "adoption-child").await;
    let events = ledger.events().await.unwrap();
    let settled = |name: &str| {
        events
            .iter()
            .rev()
            .find(|event| {
                event.contract_id.0 == name
                    && matches!(event.command.command, Command::Discharge { .. })
            })
            .unwrap()
            .seq
    };
    let request = crate::MigrationRequest {
        manifest: "manifest-digest".to_string(),
        state: state_of(&ledger).await,
        acceptances: vec![
            crate::MigratedAcceptance {
                selection: first,
                event: settled("root"),
            },
            crate::MigratedAcceptance {
                selection: second,
                event: settled("child"),
            },
        ],
        selection_to_activate: Some(second),
        authorization: "authorized".to_string(),
        identities: Identities::default(),
    };
    assert!(matches!(
        ledger.migrate(&request, "migration").await,
        Err(LedgerError::NotAnAdoption { selection, reason }) if selection == second && reason.contains("unknown")
    ));
    assert!(ledger.acceptances().await.unwrap().is_empty());
}
