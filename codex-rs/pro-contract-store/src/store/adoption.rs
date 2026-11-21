//! Fenced, atomic settlement and incumbent selection for the protected host.

use std::collections::BTreeSet;

use codex_pro_contract::AuthenticatedCommand;
use codex_pro_contract::Command;
use codex_pro_contract::Contract;
use codex_pro_contract::Digest;
use codex_pro_contract::Standing;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use sqlx::Row;

use super::experiments::ExperimentEvent;
use super::experiments::ExperimentKind;
use super::experiments::Identities;
use super::ledger::Ledger;
use super::ledger::LedgerError;
use super::ledger::storage;
use crate::digest_of;

/// An authenticated discharge fenced against the incumbent observed before confirmation.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdoptionRequest {
    pub command: AuthenticatedCommand,
    /// Latest incumbent selection's sequence number, or `None` for initial adoption.
    pub expected_incumbent_seq: Option<i64>,
    pub identities: Identities,
    /// Exact earlier acceptances this adoption relies on, each named by the sequence number of
    /// its adopting selection. All must be live when the adoption commits. Absent from requests
    /// that name none, so their digests are unchanged.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub supports: Vec<i64>,
}

/// Selects again an acceptance that is still live, fenced like an adoption. It accepts nothing.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelectionRequest {
    /// Sequence number of the adopting selection to select again.
    pub acceptance: i64,
    /// Latest incumbent selection's sequence number.
    pub expected_incumbent_seq: Option<i64>,
    pub identities: Identities,
    /// How the selecting act was obtained; recorded verbatim.
    pub decision: Value,
}

/// What a store must hold, exactly, for a migration to apply to it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationState {
    pub kernel_events: i64,
    pub kernel_head: String,
    pub journal_events: i64,
    pub journal_head: String,
}

/// An adoption an earlier binary recorded only in the journal, nominated for registration.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigratedAcceptance {
    /// Sequence number of its journal selection.
    pub selection: i64,
    /// Sequence number of the kernel event that settled it.
    pub event: i64,
}

/// Registers adoptions made before acceptances were recorded. The request only says which journal
/// selection and settling event to look at, and under whose authority. The fence an adoption
/// carried, what it was built on, and which selection was current are read from the store.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationRequest {
    /// Digest of the manifest this request was made from.
    pub manifest: String,
    pub state: MigrationState,
    pub acceptances: Vec<MigratedAcceptance>,
    /// One of the registered acceptances to record as the current selection, if any.
    pub selection_to_activate: Option<i64>,
    /// The host's receipt of the principal's authorization.
    pub authorization: String,
    pub identities: Identities,
}

/// What a migration registered.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Migrated {
    pub registered: Vec<i64>,
    pub activated: Option<i64>,
    /// Sequence number of the journal entry that records the migration.
    pub recorded: i64,
}

/// One row per acceptance, written only by the adoption transaction that settled it. A selection
/// in the journal is an acceptance exactly when it has a row here: the journal accepts appended
/// events from other writers, so its shape alone confers nothing.
pub(crate) const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS acceptances (
    selection INTEGER PRIMARY KEY NOT NULL,
    contract_id TEXT NOT NULL,
    event INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    subject_hash TEXT NOT NULL,
    supports TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS acceptances_no_update
BEFORE UPDATE ON acceptances
BEGIN SELECT RAISE(ABORT, 'acceptances are append-only'); END;
CREATE TRIGGER IF NOT EXISTS acceptances_no_delete
BEFORE DELETE ON acceptances
BEGIN SELECT RAISE(ABORT, 'acceptances are append-only'); END;
CREATE TABLE IF NOT EXISTS incumbents (
    selection INTEGER PRIMARY KEY NOT NULL,
    acceptance INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS incumbents_no_update
BEFORE UPDATE ON incumbents
BEGIN SELECT RAISE(ABORT, 'incumbents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS incumbents_no_delete
BEFORE DELETE ON incumbents
BEGIN SELECT RAISE(ABORT, 'incumbents are append-only'); END;
";

/// The latest selection made by an adoption or a re-selection transaction. Selections appended
/// to the journal by other writers are not selections of an incumbent: they neither move this
/// fence nor change what serves.
const LATEST_INCUMBENT: &str = "SELECT selection FROM incumbents ORDER BY selection DESC LIMIT 1";

/// An acceptance as the store recorded it, with its liveness now.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AcceptanceView {
    /// Sequence number of the adopting selection; the acceptance's identity.
    pub selection: i64,
    pub contract_id: String,
    pub generation: u64,
    pub subject_hash: String,
    pub supports: Vec<i64>,
    pub live: bool,
}

/// The current selection of an incumbent and the acceptance it selects.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct IncumbentView {
    /// Sequence number of the latest selection; the fence for the next one.
    pub selection: i64,
    pub acceptance: AcceptanceView,
}

struct Acceptance {
    contract_id: String,
    generation: u64,
    subject_hash: String,
    supports: Vec<i64>,
}

/// The acceptance recorded by adopting selection `seq`, or `None` when the adoption
/// transaction recorded none: a restoring selection, an event appended by another writer, or
/// an adoption made before acceptances were recorded.
async fn acceptance(
    connection: &mut sqlx::SqliteConnection,
    seq: i64,
) -> Result<Option<Acceptance>, LedgerError> {
    let row = sqlx::query(
        "SELECT contract_id, generation, subject_hash, supports FROM acceptances
         WHERE selection = ?",
    )
    .bind(seq)
    .fetch_optional(&mut *connection)
    .await
    .map_err(storage)?;
    let Some(row) = row else { return Ok(None) };
    let supports: String = row.try_get("supports").map_err(storage)?;
    Ok(Some(Acceptance {
        contract_id: row.try_get("contract_id").map_err(storage)?,
        generation: row.try_get::<i64, _>("generation").map_err(storage)? as u64,
        subject_hash: row.try_get("subject_hash").map_err(storage)?,
        supports: serde_json::from_str(&supports)
            .map_err(|error| LedgerError::Corrupt(error.to_string()))?,
    }))
}

/// An acceptance is live while its Contract is still settled by that same settlement, named by
/// its generation and subject, and every acceptance it names as support is live. Settling the
/// same content again, by any path, is another settlement. Returns the first selection found
/// not live.
async fn first_not_live(
    connection: &mut sqlx::SqliteConnection,
    root: i64,
) -> Result<Option<i64>, LedgerError> {
    let mut pending = vec![root];
    let mut seen = BTreeSet::new();
    while let Some(seq) = pending.pop() {
        if !seen.insert(seq) {
            continue;
        }
        let Some(recorded) = acceptance(connection, seq).await? else {
            return Ok(Some(seq));
        };
        let contract: Option<Contract> =
            sqlx::query_scalar::<_, String>("SELECT state_json FROM contracts WHERE id = ?")
                .bind(&recorded.contract_id)
                .fetch_optional(&mut *connection)
                .await
                .map_err(storage)?
                .map(|json| {
                    serde_json::from_str(&json)
                        .map_err(|error| LedgerError::Corrupt(error.to_string()))
                })
                .transpose()?;
        let same_settlement = contract
            .as_ref()
            .filter(|contract| contract.standing == Standing::Discharged)
            .and_then(|contract| contract.settlement.as_ref())
            .is_some_and(|settlement| {
                settlement.coordinate.generation == recorded.generation
                    && settlement.coordinate.subject_hash.to_string() == recorded.subject_hash
            });
        if !same_settlement {
            return Ok(Some(seq));
        }
        pending.extend(recorded.supports);
    }
    Ok(None)
}

async fn view(
    connection: &mut sqlx::SqliteConnection,
    selection: i64,
) -> Result<AcceptanceView, LedgerError> {
    let recorded = acceptance(connection, selection)
        .await?
        .ok_or_else(|| LedgerError::Corrupt(format!("no acceptance for selection {selection}")))?;
    Ok(AcceptanceView {
        selection,
        contract_id: recorded.contract_id,
        generation: recorded.generation,
        subject_hash: recorded.subject_hash,
        supports: recorded.supports,
        live: first_not_live(connection, selection).await?.is_none(),
    })
}

impl Ledger {
    /// Commits discharge and selection together. Identical retries return their original state,
    /// even after another adoption; changed commands, identities or fences conflict.
    pub async fn adopt(
        &self,
        request: &AdoptionRequest,
        idempotency_key: &str,
    ) -> Result<Contract, LedgerError> {
        let command_hash = digest_of("adoption", request).to_string();
        let mut tx = self
            .pool
            .begin_with("BEGIN IMMEDIATE")
            .await
            .map_err(storage)?;
        if let Some(committed) =
            Self::committed_result(&mut tx, idempotency_key, &command_hash).await?
        {
            return Ok(committed);
        }
        let Command::Discharge { coordinate, .. } = &request.command.command else {
            return Err(LedgerError::InvalidEvent(
                "adoption requires a discharge".to_string(),
            ));
        };
        let actual = sqlx::query_scalar::<_, i64>(LATEST_INCUMBENT)
            .fetch_optional(&mut *tx)
            .await
            .map_err(storage)?;
        if actual != request.expected_incumbent_seq {
            return Err(LedgerError::IncumbentChanged {
                expected: request.expected_incumbent_seq,
                actual,
            });
        }
        // Checked under the same write lock that commits the discharge: a support withdrawn
        // before this point refuses the adoption, and none can be withdrawn in between.
        for support in &request.supports {
            if let Some(selection) = first_not_live(&mut tx, *support).await? {
                return Err(LedgerError::SupportNotLive { selection });
            }
        }
        let next =
            Self::apply_in_transaction(&mut tx, &request.command, idempotency_key, &command_hash)
                .await?;
        let record = Self::append_experiment_in_transaction(
            &mut tx,
            &ExperimentEvent {
                kind: ExperimentKind::Selection,
                identities: request.identities.clone(),
                body: if request.supports.is_empty() {
                    json!({
                        "role": "incumbent",
                        "version": coordinate.subject_hash.to_string(),
                        "adoption": request.command.contract_id,
                    })
                } else {
                    json!({
                        "role": "incumbent",
                        "version": coordinate.subject_hash.to_string(),
                        "adoption": request.command.contract_id,
                        "generation": coordinate.generation,
                        "supports": request.supports,
                    })
                },
            },
        )
        .await?;
        // The acceptance itself: this selection, the kernel event that settled it, and its
        // supports. Only this transaction writes it.
        let event =
            sqlx::query_scalar::<_, i64>("SELECT seq FROM events ORDER BY seq DESC LIMIT 1")
                .fetch_one(&mut *tx)
                .await
                .map_err(storage)?;
        sqlx::query(
            "INSERT INTO acceptances (selection, contract_id, event, generation, subject_hash, supports)
             VALUES (?, ?, ?, ?, ?, ?)",
        )
        .bind(record.seq)
        .bind(&request.command.contract_id.0)
        .bind(event)
        .bind(coordinate.generation as i64)
        .bind(coordinate.subject_hash.to_string())
        .bind(json!(request.supports).to_string())
        .execute(&mut *tx)
        .await
        .map_err(storage)?;
        sqlx::query("INSERT INTO incumbents (selection, acceptance) VALUES (?, ?)")
            .bind(record.seq)
            .bind(record.seq)
            .execute(&mut *tx)
            .await
            .map_err(storage)?;
        tx.commit().await.map_err(storage)?;
        Ok(next)
    }

    /// Whether the acceptance recorded by adopting selection `seq` is live now.
    pub async fn live(&self, seq: i64) -> Result<bool, LedgerError> {
        let mut connection = self.pool.acquire().await.map_err(storage)?;
        Ok(first_not_live(&mut connection, seq).await?.is_none())
    }

    /// Every acceptance the store recorded, in order, with its liveness in one snapshot.
    pub async fn acceptances(&self) -> Result<Vec<AcceptanceView>, LedgerError> {
        let mut tx = self.pool.begin().await.map_err(storage)?;
        let selections =
            sqlx::query_scalar::<_, i64>("SELECT selection FROM acceptances ORDER BY selection")
                .fetch_all(&mut *tx)
                .await
                .map_err(storage)?;
        let mut views = Vec::with_capacity(selections.len());
        for selection in selections {
            views.push(view(&mut tx, selection).await?);
        }
        Ok(views)
    }

    /// The current selection of an incumbent, as made by an adoption or a re-selection, with
    /// the acceptance it selects and whether that acceptance is live. `None` before any.
    pub async fn incumbent(&self) -> Result<Option<IncumbentView>, LedgerError> {
        let mut tx = self.pool.begin().await.map_err(storage)?;
        let row = sqlx::query(
            "SELECT selection, acceptance FROM incumbents ORDER BY selection DESC LIMIT 1",
        )
        .fetch_optional(&mut *tx)
        .await
        .map_err(storage)?;
        let Some(row) = row else { return Ok(None) };
        let acceptance: i64 = row.try_get("acceptance").map_err(storage)?;
        Ok(Some(IncumbentView {
            selection: row.try_get("selection").map_err(storage)?,
            acceptance: view(&mut tx, acceptance).await?,
        }))
    }

    /// Records a new incumbent selection of a live acceptance, under the incumbent fence, in one
    /// transaction. Identical retries return the original sequence number.
    pub async fn select(
        &self,
        request: &SelectionRequest,
        idempotency_key: &str,
    ) -> Result<i64, LedgerError> {
        let command_hash = digest_of("selection", request).to_string();
        let mut tx = self
            .pool
            .begin_with("BEGIN IMMEDIATE")
            .await
            .map_err(storage)?;
        let committed =
            sqlx::query("SELECT command_hash, result_json FROM idempotency WHERE key = ?")
                .bind(idempotency_key)
                .fetch_optional(&mut *tx)
                .await
                .map_err(storage)?;
        if let Some(row) = committed {
            let stored: String = row.try_get("command_hash").map_err(storage)?;
            if stored != command_hash {
                return Err(LedgerError::IdempotencyConflict);
            }
            let result: String = row.try_get("result_json").map_err(storage)?;
            let value: Value = serde_json::from_str(&result)
                .map_err(|error| LedgerError::Corrupt(error.to_string()))?;
            return value["selection"]
                .as_i64()
                .ok_or_else(|| LedgerError::Corrupt("selection result".to_string()));
        }
        let actual = sqlx::query_scalar::<_, i64>(LATEST_INCUMBENT)
            .fetch_optional(&mut *tx)
            .await
            .map_err(storage)?;
        if actual != request.expected_incumbent_seq {
            return Err(LedgerError::IncumbentChanged {
                expected: request.expected_incumbent_seq,
                actual,
            });
        }
        if let Some(selection) = first_not_live(&mut tx, request.acceptance).await? {
            return Err(LedgerError::SupportNotLive { selection });
        }
        let Some(original) = acceptance(&mut tx, request.acceptance).await? else {
            return Err(LedgerError::SupportNotLive {
                selection: request.acceptance,
            });
        };
        let record = Self::append_experiment_in_transaction(
            &mut tx,
            &ExperimentEvent {
                kind: ExperimentKind::Selection,
                identities: request.identities.clone(),
                body: json!({
                    "role": "incumbent",
                    "version": original.subject_hash,
                    "adoption": original.contract_id,
                    "restores": request.acceptance,
                    "decision": request.decision,
                }),
            },
        )
        .await?;
        sqlx::query("INSERT INTO incumbents (selection, acceptance) VALUES (?, ?)")
            .bind(record.seq)
            .bind(request.acceptance)
            .execute(&mut *tx)
            .await
            .map_err(storage)?;
        sqlx::query("INSERT INTO idempotency (key, command_hash, result_json) VALUES (?, ?, ?)")
            .bind(idempotency_key)
            .bind(&command_hash)
            .bind(json!({ "selection": record.seq }).to_string())
            .execute(&mut *tx)
            .await
            .map_err(storage)?;
        tx.commit().await.map_err(storage)?;
        Ok(record.seq)
    }

    /// Registers, in one transaction, adoptions that an earlier binary recorded only in the
    /// journal, and optionally records one of them as the current selection.
    ///
    /// Nothing is taken from the request on trust. The store must hold exactly the stated
    /// state and no acceptance yet. For each nominated adoption the store reads the fence the
    /// earlier protocol used at that position of the journal, rebuilds the old adoption request
    /// from the settling kernel event, the journal selection and that fence, and requires that
    /// its `adoption`-domain digest was retained with the state that event produced: a plain
    /// settlement, an appended journal entry or a retained request moved to another position
    /// has no such record. Only an adoption that expected no incumbent is registered, because
    /// only it is known to rest on no earlier adoption; the rest stay unknown. The selection
    /// to activate must be the store's own latest: a migration restores, it does not choose.
    /// Any failure changes nothing. Identical retries return the original result.
    pub async fn migrate(
        &self,
        request: &MigrationRequest,
        idempotency_key: &str,
    ) -> Result<Migrated, LedgerError> {
        let invalid = |message: String| LedgerError::InvalidEvent(message);
        let command_hash = digest_of("migration", request).to_string();
        let mut tx = self
            .pool
            .begin_with("BEGIN IMMEDIATE")
            .await
            .map_err(storage)?;
        let committed =
            sqlx::query("SELECT command_hash, result_json FROM idempotency WHERE key = ?")
                .bind(idempotency_key)
                .fetch_optional(&mut *tx)
                .await
                .map_err(storage)?;
        if let Some(row) = committed {
            let stored: String = row.try_get("command_hash").map_err(storage)?;
            if stored != command_hash {
                return Err(LedgerError::IdempotencyConflict);
            }
            let result: String = row.try_get("result_json").map_err(storage)?;
            return serde_json::from_str(&result)
                .map_err(|error| LedgerError::Corrupt(error.to_string()));
        }
        if request.authorization.trim().is_empty() {
            return Err(invalid(
                "a migration needs the principal's authorization".to_string(),
            ));
        }
        let kernel = sqlx::query(
            "SELECT count(*) AS n, (SELECT hash FROM events ORDER BY seq DESC LIMIT 1) AS head
             FROM events",
        )
        .fetch_one(&mut *tx)
        .await
        .map_err(storage)?;
        let journal = sqlx::query(
            "SELECT count(*) AS n,
             (SELECT hash FROM experiment_events ORDER BY seq DESC LIMIT 1) AS head
             FROM experiment_events",
        )
        .fetch_one(&mut *tx)
        .await
        .map_err(storage)?;
        let zero = Digest::from_bytes([0; 32]).to_string();
        let actual = MigrationState {
            kernel_events: kernel.try_get("n").map_err(storage)?,
            kernel_head: kernel
                .try_get::<Option<String>, _>("head")
                .map_err(storage)?
                .unwrap_or_else(|| zero.clone()),
            journal_events: journal.try_get("n").map_err(storage)?,
            journal_head: journal
                .try_get::<Option<String>, _>("head")
                .map_err(storage)?
                .unwrap_or(zero),
        };
        if actual != request.state {
            return Err(LedgerError::MigrationStateChanged);
        }
        let existing = sqlx::query_scalar::<_, i64>(
            "SELECT (SELECT count(*) FROM acceptances) + (SELECT count(*) FROM incumbents)",
        )
        .fetch_one(&mut *tx)
        .await
        .map_err(storage)?;
        if existing != 0 {
            return Err(invalid(
                "this store already records acceptances".to_string(),
            ));
        }
        let mut registered = Vec::new();
        let mut used = Vec::new();
        for nominated in &request.acceptances {
            let not_adoption = |reason: &str| LedgerError::NotAnAdoption {
                selection: nominated.selection,
                reason: reason.to_string(),
            };
            let entry = sqlx::query_scalar::<_, String>(
                "SELECT event_json FROM experiment_events WHERE seq = ? AND kind = 'selection'",
            )
            .bind(nominated.selection)
            .fetch_optional(&mut *tx)
            .await
            .map_err(storage)?
            .ok_or_else(|| not_adoption("no such journal selection"))?;
            let entry: ExperimentEvent = serde_json::from_str(&entry)
                .map_err(|error| LedgerError::Corrupt(error.to_string()))?;
            if entry.body["role"] != "incumbent" {
                return Err(not_adoption("the journal entry selects no incumbent"));
            }
            let settled = sqlx::query(
                "SELECT contract_id, command_json, state_hash FROM events WHERE seq = ?",
            )
            .bind(nominated.event)
            .fetch_optional(&mut *tx)
            .await
            .map_err(storage)?
            .ok_or_else(|| not_adoption("no such kernel event"))?;
            let contract_id: String = settled.try_get("contract_id").map_err(storage)?;
            let command: AuthenticatedCommand = serde_json::from_str(
                &settled
                    .try_get::<String, _>("command_json")
                    .map_err(storage)?,
            )
            .map_err(|error| LedgerError::Corrupt(error.to_string()))?;
            let Command::Discharge { coordinate, .. } = &command.command else {
                return Err(not_adoption("the kernel event is not a settlement"));
            };
            if entry.body["adoption"].as_str() != Some(contract_id.as_str())
                || entry.body["version"].as_str()
                    != Some(coordinate.subject_hash.to_string().as_str())
            {
                return Err(not_adoption(
                    "the journal entry and the kernel event name different work",
                ));
            }
            // The fence the earlier protocol used was the latest incumbent-shaped journal entry
            // before this one, whoever wrote it. It is read here, never taken from the request,
            // so a retained request cannot be reused at another position in the journal.
            let fence = sqlx::query_scalar::<_, Option<i64>>(
                "SELECT max(seq) FROM experiment_events WHERE kind = 'selection'
                 AND json_extract(event_json, '$.body.role') = 'incumbent' AND seq < ?",
            )
            .bind(nominated.selection)
            .fetch_one(&mut *tx)
            .await
            .map_err(storage)?;
            // The request the earlier binary hashed when this adoption committed.
            let original = AdoptionRequest {
                command: command.clone(),
                expected_incumbent_seq: fence,
                identities: entry.identities.clone(),
                supports: Vec::new(),
            };
            let retained = sqlx::query_scalar::<_, String>(
                "SELECT result_json FROM idempotency WHERE command_hash = ?",
            )
            .bind(digest_of("adoption", &original).to_string())
            .fetch_optional(&mut *tx)
            .await
            .map_err(storage)?
            .ok_or_else(|| {
                not_adoption("no adoption request was retained for it at this position")
            })?;
            let result: Contract = serde_json::from_str(&retained)
                .map_err(|error| LedgerError::Corrupt(error.to_string()))?;
            let state_hash: String = settled.try_get("state_hash").map_err(storage)?;
            if digest_of("contract_state", &result).to_string() != state_hash {
                return Err(not_adoption(
                    "the retained result is not the state the settlement produced",
                ));
            }
            // The earlier request named nothing it was built on. Only an adoption that expected
            // no incumbent is known to rest on no earlier adoption of this store; any other is
            // a real adoption whose supports are unknown, and unknown is not registered.
            if fence.is_some() {
                return Err(not_adoption(
                    "what it was built on is not recorded; it stays unknown",
                ));
            }
            if used.contains(&nominated.event) {
                return Err(not_adoption(
                    "its settling event already backs another nomination",
                ));
            }
            used.push(nominated.event);
            sqlx::query(
                "INSERT INTO acceptances (selection, contract_id, event, generation, subject_hash, supports)
                 VALUES (?, ?, ?, ?, ?, '[]')",
            )
            .bind(nominated.selection)
            .bind(&contract_id)
            .bind(nominated.event)
            .bind(coordinate.generation as i64)
            .bind(coordinate.subject_hash.to_string())
            .execute(&mut *tx)
            .await
            .map_err(storage)?;
            registered.push(nominated.selection);
        }
        if let Some(selection) = request.selection_to_activate {
            // A migration restores what was current; it does not choose. The selection to
            // activate must be the store's latest incumbent-shaped entry. Selecting an earlier
            // acceptance instead is a new selection, made through `select`.
            let current = sqlx::query_scalar::<_, Option<i64>>(
                "SELECT max(seq) FROM experiment_events WHERE kind = 'selection'
                 AND json_extract(event_json, '$.body.role') = 'incumbent'",
            )
            .fetch_one(&mut *tx)
            .await
            .map_err(storage)?;
            if current != Some(selection) {
                return Err(LedgerError::NotAnAdoption {
                    selection,
                    reason: "it was not the store's current selection; choosing it is a new selection, not a migration".to_string(),
                });
            }
            if !registered.contains(&selection)
                || first_not_live(&mut tx, selection).await?.is_some()
            {
                return Err(LedgerError::SupportNotLive { selection });
            }
            sqlx::query("INSERT INTO incumbents (selection, acceptance) VALUES (?, ?)")
                .bind(selection)
                .bind(selection)
                .execute(&mut *tx)
                .await
                .map_err(storage)?;
        }
        let record = Self::append_experiment_in_transaction(
            &mut tx,
            &ExperimentEvent {
                kind: ExperimentKind::Insight,
                identities: request.identities.clone(),
                body: json!({
                    "migration": {
                        "manifest": request.manifest,
                        "authorization": request.authorization,
                        "registered": registered,
                        "activated": request.selection_to_activate,
                    }
                }),
            },
        )
        .await?;
        let migrated = Migrated {
            registered,
            activated: request.selection_to_activate,
            recorded: record.seq,
        };
        sqlx::query("INSERT INTO idempotency (key, command_hash, result_json) VALUES (?, ?, ?)")
            .bind(idempotency_key)
            .bind(&command_hash)
            .bind(serde_json::to_string(&migrated).map_err(storage)?)
            .execute(&mut *tx)
            .await
            .map_err(storage)?;
        tx.commit().await.map_err(storage)?;
        Ok(migrated)
    }
}
