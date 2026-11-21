//! Host-only bridge to the actual native public archive query implementation.
//!
//! The caller owns the ledger connection and capture barrier. This module opens
//! no database/path, runs no model and never calls the observed (writing) tool
//! entrypoint. Expected boundaries are pins, not declarations of external closure.

use std::collections::{BTreeMap, BTreeSet};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use codex_pro_contract::{ContractId, Digest};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::archive::{EvidenceAccess, Query};
use super::work::PublicEvidence;
use crate::{BlobStore, Ledger};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelectorArchiveInvocation {
    pub contract_id: ContractId,
    pub assessment: Digest,
    pub selected_subject: Digest,
    pub delivery: Option<Digest>,
}

/// An explicit export allocation, not a task or model workflow policy.
pub struct ArchiveCaptureLimits {
    pub max_entries: usize,
    pub max_bytes: usize,
}

pub struct SelectorArchiveReader {
    access: EvidenceAccess,
    expected: Value,
}

impl SelectorArchiveReader {
    /// Bind the exact archive object installed in a native selector, without rebuilding sources.
    pub(crate) async fn from_access(access: EvidenceAccess) -> Result<Self, String> {
        let expected = Self::boundary(&access).await?;
        let reader = Self { access, expected };
        reader
            .query(json!({"op":"read","source_id":"assessment"}), 8192)
            .await?;
        Ok(reader)
    }

    fn access(
        ledger: Ledger,
        store: BlobStore,
        invocation: &SelectorArchiveInvocation,
    ) -> Result<EvidenceAccess, String> {
        let public: PublicEvidence = serde_json::from_slice(
            &store
                .get(&invocation.assessment)
                .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        EvidenceAccess {
            contract_id: invocation.contract_id.clone(),
            ledger,
            store,
            observations: Default::default(),
            named_sources: Default::default(),
            selected_subject: None,
        }
        .with_selection_sources(
            invocation.assessment,
            &public,
            invocation.selected_subject,
            invocation.delivery,
        )
    }

    async fn boundary(access: &EvidenceAccess) -> Result<Value, String> {
        access
            .ledger
            .verify_experiment_chain()
            .await
            .map_err(|e| e.to_string())?;
        let contract = access
            .ledger
            .contract(&access.contract_id)
            .await
            .map_err(|e| e.to_string())?
            .ok_or("responsibility has not been issued")?;
        let events = access.ledger.events().await.map_err(|e| e.to_string())?;
        let journal = access
            .ledger
            .experiments()
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({
            "contract": contract,
            "kernel_head": events.last().map(|row| json!({"seq":row.seq,"hash":row.hash})),
            "journal_head": journal.last().map(|row| json!({"seq":row.seq,"hash":row.hash})),
            "sources": access.source_bindings(), "selected_subject": access.selected_subject,
        }))
    }

    /// Obtain the boundary under the host's capture barrier, then bind it to the
    /// parent outcome/payload before supplying it to `bind`. Not an as-of rewind.
    pub async fn observe_boundary(
        ledger: Ledger,
        store: BlobStore,
        invocation: &SelectorArchiveInvocation,
    ) -> Result<Value, String> {
        Self::boundary(&Self::access(ledger, store, invocation)?).await
    }

    pub async fn bind(
        ledger: Ledger,
        store: BlobStore,
        invocation: &SelectorArchiveInvocation,
        expected: Value,
    ) -> Result<Self, String> {
        let reader = Self {
            access: Self::access(ledger, store, invocation)?,
            expected,
        };
        reader.check_boundary().await?;
        // Naming a source does not make it public: exercise the actual native
        // membership and subject check, even if a later export has no read calls.
        reader
            .query(json!({"op":"read","source_id":"assessment"}), 8192)
            .await?;
        Ok(reader)
    }

    pub(crate) async fn check_boundary(&self) -> Result<(), String> {
        if Self::boundary(&self.access).await? != self.expected {
            return Err("selector archive boundary changed".into());
        }
        Ok(())
    }

    pub async fn query(&self, query: Value, budget: usize) -> Result<Value, String> {
        if query.to_string().len() > 4096 {
            return Err("evidence query exceeds 4 KiB".into());
        }
        let query: Query = serde_json::from_value(query).map_err(|e| e.to_string())?;
        self.check_boundary().await?;
        let result = self.access.read(query, budget).await;
        // Drift remains a capture failure even when the query itself was denied.
        self.check_boundary().await?;
        result
    }

    /// Export the exact `frozen-public-evidence/v1` body consumed by the Python
    /// frozen view. Only the native reader decides membership/frontier/sources.
    pub async fn export(&self, limits: ArchiveCaptureLimits) -> Result<Value, String> {
        self.check_boundary().await?;
        let mut pages = BTreeMap::new();
        for op in ["index", "frontier", "sources"] {
            let mut entries = Vec::new();
            let mut offset = 0;
            loop {
                let page = self.query(json!({"op":op,"offset":offset}), 8192).await?;
                let total = page["total"].as_u64().ok_or("native page has no total")? as usize;
                if total > limits.max_entries {
                    return Err("archive entry allocation exceeded".into());
                }
                entries.extend(
                    page["entries"]
                        .as_array()
                        .ok_or("native page has no entries")?
                        .iter()
                        .cloned(),
                );
                let next = page["next_offset"]
                    .as_u64()
                    .ok_or("native page has no cursor")? as usize;
                if page["eof"] == true {
                    break;
                }
                if next <= offset {
                    return Err("native index did not advance".into());
                }
                offset = next;
            }
            pages.insert(op.to_string(), entries);
        }
        let mut handles = BTreeSet::<Digest>::new();
        for entry in &pages["index"] {
            handles.insert(
                serde_json::from_value(entry["handle"].clone()).map_err(|e| e.to_string())?,
            );
            for field in ["delivery_message", "host_observations"] {
                if !entry[field].is_null() {
                    handles.insert(
                        serde_json::from_value(entry[field].clone()).map_err(|e| e.to_string())?,
                    );
                }
            }
            for field in ["inspections", "review_checkpoints"] {
                if let Some(values) = entry[field].as_array() {
                    for value in values {
                        handles.insert(
                            serde_json::from_value(value.clone()).map_err(|e| e.to_string())?,
                        );
                    }
                }
            }
        }
        for (op, field) in [("sources", "handle"), ("frontier", "evidence")] {
            for entry in &pages[op] {
                handles.insert(
                    serde_json::from_value(entry[field].clone()).map_err(|e| e.to_string())?,
                );
            }
        }
        let mut blobs = BTreeMap::new();
        let mut denied = BTreeMap::new();
        let mut used = 0usize;
        for handle in handles {
            let mut bytes = Vec::new();
            loop {
                let page = match self
                    .query(
                        json!({"op":"read","handle":handle,"offset":bytes.len()}),
                        8192,
                    )
                    .await
                {
                    Ok(page) => page,
                    Err(reason)
                        if bytes.is_empty()
                            && reason
                                == "evidence handle is not public evidence of this responsibility" =>
                    {
                        denied.insert(handle.to_string(), reason);
                        break;
                    }
                    Err(reason) => return Err(reason),
                };
                let total = page["total_bytes"]
                    .as_u64()
                    .ok_or("native page has no byte count")? as usize;
                if total > limits.max_bytes.saturating_sub(used) {
                    return Err("archive byte allocation exceeded".into());
                }
                let part = page["text"].as_str().ok_or("native page has no text")?;
                let prior = bytes.len();
                bytes.extend_from_slice(part.as_bytes());
                if page["eof"] == true {
                    if bytes.len() != total || Digest::of(&bytes) != handle {
                        return Err("native archive bytes differ from their handle".into());
                    }
                    used += bytes.len();
                    blobs.insert(handle.to_string(), STANDARD.encode(&bytes));
                    break;
                }
                if bytes.len() <= prior {
                    return Err("native byte page did not advance".into());
                }
            }
        }
        self.check_boundary().await?;
        Ok(
            json!({"schema":"frozen-public-evidence/v1","boundary":self.expected,
                  "pages":pages,"blobs":blobs,"denied":denied}),
        )
    }
}
