//! Historical acceptance dependencies, independent of selection and liveness.
//!
//! This is the graph primitive for record-level progress. It is not yet wired
//! into the V1 reducer or store. Authenticated transitions must check live support
//! and commit invalidation/selection changes atomically at the storage boundary.

use std::collections::BTreeMap;
use std::collections::BTreeSet;

use serde::Deserialize;
use serde::Serialize;

use crate::ContractId;
use crate::Digest;

/// Identity of an immutable authorized acceptance event, never an artifact hash.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct AcceptanceId(pub Digest);

/// A formally accepted judgment and the exact earlier acceptances it relies on.
/// Support alone does not create this record; the caller authenticates Discharge.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct AcceptanceRecord {
    pub id: AcceptanceId,
    pub contract_id: ContractId,
    pub revision: u32,
    pub subject_hash: Digest,
    pub judgment_hash: Digest,
    pub supports: BTreeSet<AcceptanceId>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RecordGraphError {
    DuplicateRecord(AcceptanceId),
    UnknownRecord(AcceptanceId),
}

/// Append-only record topology. Current liveness and selection belong to the
/// authoritative task projection; they must never prune this historical graph.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AcceptanceGraph {
    records: BTreeMap<AcceptanceId, AcceptanceRecord>,
    dependents: BTreeMap<AcceptanceId, BTreeSet<AcceptanceId>>,
}

impl AcceptanceGraph {
    /// Admit topology only after its referenced records exist. This order excludes
    /// cycles. A caller separately checks that all supports are currently live.
    pub fn insert(&mut self, record: AcceptanceRecord) -> Result<(), RecordGraphError> {
        if self.records.contains_key(&record.id) {
            return Err(RecordGraphError::DuplicateRecord(record.id));
        }
        for parent in &record.supports {
            if !self.records.contains_key(parent) {
                return Err(RecordGraphError::UnknownRecord(*parent));
            }
        }
        // No mutation before all references are validated.
        for parent in &record.supports {
            self.dependents
                .entry(*parent)
                .or_default()
                .insert(record.id);
        }
        self.records.insert(record.id, record);
        Ok(())
    }

    pub fn get(&self, id: AcceptanceId) -> Option<&AcceptanceRecord> {
        self.records.get(&id)
    }

    /// Revise/Release seed every historical acceptance of the task, including
    /// unselected and already invalidated ones. No fallback is chosen here.
    pub fn records_for(&self, contract: &ContractId) -> BTreeSet<AcceptanceId> {
        self.records
            .values()
            .filter(|r| &r.contract_id == contract)
            .map(|r| r.id)
            .collect()
    }

    /// Entire descendant closure, including each seed. Inactive intermediate
    /// records still carry edges. Fresh acceptance of the same bytes has a new
    /// identity and therefore cannot revive or inherit an older record's edges.
    pub fn affected(
        &self,
        seeds: &BTreeSet<AcceptanceId>,
    ) -> Result<BTreeSet<AcceptanceId>, RecordGraphError> {
        for seed in seeds {
            if !self.records.contains_key(seed) {
                return Err(RecordGraphError::UnknownRecord(*seed));
            }
        }
        let mut affected = seeds.clone();
        let mut pending: Vec<_> = seeds.iter().copied().collect();
        while let Some(parent) = pending.pop() {
            if let Some(children) = self.dependents.get(&parent) {
                for child in children {
                    if affected.insert(*child) {
                        pending.push(*child);
                    }
                }
            }
        }
        Ok(affected)
    }
}

#[cfg(test)]
#[path = "records_tests.rs"]
mod tests;
