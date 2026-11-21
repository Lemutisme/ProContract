//! Read-only public evidence access bound by the host to one responsibility.
//! The immutable verification journal is the index; a model never chooses a store path.

use codex_pro_contract::Contract;
use codex_pro_contract::ContractId;
use codex_pro_contract::Digest;
use codex_pro_contract_store::ExperimentKind;
use serde::Deserialize;
use serde_json::Value;
use serde_json::json;

use super::work;
use super::work::PublicEvidence;
use crate::BlobStore;
use crate::Ledger;

#[derive(Clone)]
pub(crate) struct EvidenceAccess {
    pub(super) contract_id: ContractId,
    pub(super) ledger: Ledger,
    pub(super) store: BlobStore,
    pub(super) observations: super::observations::AccessRecorder,
    pub(super) named_sources: std::collections::BTreeMap<String, NamedSource>,
    pub(super) selected_subject: Option<Digest>,
}

#[derive(Clone)]
pub(super) struct NamedSource {
    handle: Digest,
    subject: Digest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Query {
    pub op: Operation,
    #[serde(default)]
    pub handle: Option<Digest>,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub offset: usize,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Operation {
    Index,
    Frontier,
    Sources,
    Read,
}

impl EvidenceAccess {
    /// Names belong to this one selector invocation. Their values never follow
    /// a later "current" record, and naming a handle does not make it public.
    pub(super) fn with_selection_sources(
        mut self,
        public_handle: Digest,
        public: &PublicEvidence,
        selected_subject: Digest,
        delivery: Option<Digest>,
    ) -> Result<Self, String> {
        if public.contract_id != self.contract_id {
            return Err("selected sources belong to another responsibility".into());
        }
        self.named_sources.clear();
        self.selected_subject = Some(selected_subject);
        self.named_sources.insert(
            "assessment".into(),
            NamedSource {
                handle: public_handle,
                subject: public.subject_hash,
            },
        );
        if let Some(handle) = delivery {
            self.named_sources.insert(
                "delivery".into(),
                NamedSource {
                    handle,
                    subject: selected_subject,
                },
            );
        }
        if let Some(handle) = public
            .delivery_message
            .filter(|handle| Some(*handle) != delivery)
        {
            self.named_sources.insert(
                "assessed_delivery".into(),
                NamedSource {
                    handle,
                    subject: public.subject_hash,
                },
            );
        }
        if let Some(handle) = public.host_observations {
            self.named_sources.insert(
                "observations".into(),
                NamedSource {
                    handle,
                    subject: public.subject_hash,
                },
            );
        }
        for (index, handle) in public.inspections.iter().enumerate() {
            self.named_sources.insert(
                format!("computation:{index}"),
                NamedSource {
                    handle: *handle,
                    subject: public.subject_hash,
                },
            );
        }
        Ok(self)
    }

    pub(super) fn source_bindings(&self) -> Value {
        json!(
            self.named_sources
                .iter()
                .map(|(name, source)| {
                    (
                        name,
                        json!({"handle":source.handle,"source_subject_hash":source.subject}),
                    )
                })
                .collect::<std::collections::BTreeMap<_, _>>()
        )
    }

    async fn records(&self) -> Result<(Contract, Vec<(Digest, PublicEvidence, Vec<u8>)>), String> {
        let contract = self
            .ledger
            .contract(&self.contract_id)
            .await
            .map_err(|e| e.to_string())?
            .ok_or("responsibility has not been issued")?;
        self.ledger
            .verify_experiment_chain()
            .await
            .map_err(|e| e.to_string())?;
        let journal = self.ledger.experiments().await.map_err(|e| e.to_string())?;
        let mut records = Vec::new();
        for row in journal {
            if !(row.event.kind == ExperimentKind::Verification
                || (row.event.kind == ExperimentKind::Execution
                    && matches!(
                        row.event.body["phase"].as_str(),
                        Some("handoff_ready" | "work_ready" | "work_assessed")
                    )))
                || row.event.body["contract_id"].as_str() != Some(&self.contract_id.0)
            {
                continue;
            }
            let Some(value) = row.event.body.get("public_evidence") else {
                continue;
            };
            let handle: Digest =
                serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
            let bytes = self.store.get(&handle).map_err(|e| e.to_string())?;
            let record: PublicEvidence =
                serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if record.contract_id != self.contract_id
                || row.event.body["generation"].as_u64() != Some(record.generation)
                || serde_json::to_value(record.subject_hash).map_err(|e| e.to_string())?
                    != row.event.body["subject_hash"]
            {
                return Err("public evidence differs from its verification binding".into());
            }
            records.push((handle, record, bytes));
        }
        Ok((contract, records))
    }

    /// A check-only observation must not silently erase a known scope dispute.
    pub(super) async fn review_history(&self) -> Result<Vec<PublicEvidence>, String> {
        Ok(self
            .review_history_entries()
            .await?
            .into_iter()
            .map(|(_, record, _)| record)
            .collect())
    }

    async fn review_history_entries(&self) -> Result<Vec<(Digest, PublicEvidence, Value)>, String> {
        let (contract, records) = self.records().await?;
        let mut by_handle: std::collections::BTreeMap<_, _> = records
            .into_iter()
            .map(|(handle, record, _)| (handle, (record, json!({}))))
            .collect();
        for (handle, document, _) in self.progress_records().await? {
            let record: PublicEvidence =
                serde_json::from_value(document["public"].clone()).map_err(|e| e.to_string())?;
            if record.contract_id != self.contract_id
                || json!(record.subject_hash) != document["subject_hash"]
            {
                return Err("progress review differs from its published subject".into());
            }
            by_handle.insert(handle, (record, json!({
                "scope":document["scope"],"assessment_request":document["assessment_request"],
                "assessment_policy_hash":document["assessment_policy_hash"],
                "checks_policy_hash":document["checks_policy_hash"],"effects_resolved":document["effects_resolved"]
            })));
        }
        let mut history = Vec::new();
        for row in self
            .ledger
            .experiments()
            .await
            .map_err(|e| e.to_string())?
            .into_iter()
            .rev()
        {
            // A later selector may reference an old handle. That reference does
            // not produce the assessment or replace its original question/scope.
            if !(row.event.kind == ExperimentKind::Verification
                || (row.event.kind == ExperimentKind::Execution
                    && matches!(
                        row.event.body["phase"].as_str(),
                        Some("work_assessed" | "progress_assessed")
                    )))
                || row.event.body["contract_id"].as_str() != Some(&self.contract_id.0)
            {
                continue;
            }
            let Some(value) = row.event.body.get("public_evidence") else {
                continue;
            };
            let Ok(handle) = serde_json::from_value::<Digest>(value.clone()) else {
                continue;
            };
            if let Some((record, mut context)) = by_handle.remove(&handle)
                && record.revision == contract.revision
                && record.review.is_some()
            {
                context["assessment_event"] = json!(row.hash);
                context["assessment_sequence"] = json!(row.seq);
                if let Some(question) = row.event.body.get("question") {
                    context["question"] = question.clone();
                }
                history.push((handle, record, context));
            }
        }
        history.reverse();
        // A delayed judgment of an older generation must not supersede a newer one.
        history.sort_by_key(|(_, record, _)| record.generation);
        Ok(history)
    }

    /// One retained public assessment is offered through the existing paged
    /// evidence reader, not copied into the worker prompt or promoted to proof.
    pub(super) async fn latest_review_context(
        &self,
        current: &Contract,
        subject: Digest,
    ) -> Result<Option<Value>, String> {
        if current.id != self.contract_id {
            return Err("review context belongs to another responsibility".into());
        }
        Ok(self
            .review_history_entries()
            .await?
            .into_iter()
            .rev()
            .find(|(_, record, _)| {
                record.revision == current.revision && record.generation <= current.generation
            })
            .map(|(handle, record, context)| {
                json!({"handle":handle,
                    "current_subject_hash":subject,
                    "same_subject":record.subject_hash == subject,
                    "operation_context":context,
                    "document":{"subject_hash":record.subject_hash,"public":record},
                    "meaning":"Retained public observations and judgments under their recorded subject, scope and method. This context does not certify the current subject or grant authority; conflicting observations may revise its conclusions."
                })
            }))
    }

    /// Host-published computations, including diagnostics whose reviewer never
    /// finished. A judgment is not the source index and does not authorize reuse.
    /// The reader separately checks each receipt's immutable conditions.
    pub(super) async fn inspection_sources(
        &self,
        current: &Contract,
        subject: Digest,
    ) -> Result<Vec<Digest>, String> {
        if current.id != self.contract_id {
            return Err("inspection sources belong to another responsibility".into());
        }
        let (_, records) = self.records().await?;
        let mut records: Vec<_> = records.into_iter().map(|(_, record, _)| record).collect();
        for (_, document, _) in self.progress_records().await? {
            let record: PublicEvidence =
                serde_json::from_value(document["public"].clone()).map_err(|e| e.to_string())?;
            if record.contract_id != self.contract_id
                || json!(record.subject_hash) != document["subject_hash"]
            {
                return Err("progress computation differs from its published subject".into());
            }
            records.push(record);
        }
        Ok(records
            .into_iter()
            .filter(|record| {
                record.revision == current.revision
                    && record.generation <= current.generation
                    && record.subject_hash == subject
            })
            .flat_map(|record| record.inspections)
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect())
    }

    async fn progress_records(&self) -> Result<Vec<(Digest, Value, Vec<u8>)>, String> {
        let mut records = Vec::new();
        for row in self.ledger.experiments().await.map_err(|e| e.to_string())? {
            if row.event.kind != ExperimentKind::Execution
                || row.event.body["phase"] != "progress_assessed"
                || row.event.body["contract_id"].as_str() != Some(&self.contract_id.0)
            {
                continue;
            }
            let handle: Digest = serde_json::from_value(row.event.body["public_evidence"].clone())
                .map_err(|e| e.to_string())?;
            let bytes = self.store.get(&handle).map_err(|e| e.to_string())?;
            let document: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if document["contract_id"] != row.event.body["contract_id"]
                || document["subject_hash"] != row.event.body["subject_hash"]
                || document["publication"] != row.event.body["publication"]
                || document["scope"] != "progress_only"
            {
                return Err("progress evidence differs from its committed binding".into());
            }
            records.push((handle, document, bytes));
        }
        Ok(records)
    }

    async fn checkpoint_records(&self) -> Result<Vec<(Digest, Value, Vec<u8>)>, String> {
        let mut records = Vec::new();
        for row in self.ledger.experiments().await.map_err(|e| e.to_string())? {
            let body = row.event.body;
            if row.event.kind != ExperimentKind::Execution
                || body["phase"] != "review_checkpoint"
                || body["binding"]["formal"] != false
                || body["binding"]["contract_id"].as_str() != Some(&self.contract_id.0)
            {
                continue;
            }
            let handle =
                serde_json::from_value(body["handle"].clone()).map_err(|e| e.to_string())?;
            let bytes = self.store.get(&handle).map_err(|e| e.to_string())?;
            let document: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if document["kind"] != "review_checkpoint_v1"
                || document["scope"] != "diagnostic_checkpoint"
                || document["binding"] != body["binding"]
                || document["subject_hash"] != body["subject_hash"]
                || document["checkpoint_id"] != body["checkpoint_id"]
                || document["input_hash"] != body["input_hash"]
            {
                return Err("diagnostic checkpoint differs from its committed binding".into());
            }
            records.push((handle, document, bytes));
        }
        Ok(records)
    }

    /// Operation failures are public execution evidence, not snapshot judgments.
    async fn publication_failures(&self) -> Result<Vec<(Digest, Value, Vec<u8>)>, String> {
        let mut records = Vec::new();
        for row in self.ledger.experiments().await.map_err(|e| e.to_string())? {
            let body = row.event.body;
            if row.event.kind != ExperimentKind::Execution
                || body["phase"] != "progress_failed"
                || body["contract_id"].as_str() != Some(&self.contract_id.0)
            {
                continue;
            }
            let handle: Digest = serde_json::from_value(body["public_evidence"].clone())
                .map_err(|e| e.to_string())?;
            let bytes = self.store.get(&handle).map_err(|e| e.to_string())?;
            let document: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if document["scope"] != "publication_operation"
                || document["effects_resolved"] != true
                || document["contract_id"] != body["contract_id"]
                || document["publication"] != body["publication"]
                || document["request_id"] != body["request_id"]
                || !document["subject_hash"].is_null()
                || !body["subject_hash"].is_null()
            {
                return Err("publication failure differs from its committed binding".into());
            }
            records.push((handle, document, bytes));
        }
        Ok(records)
    }

    /// Only task-public diagnostic work can seed a continuation. It remains historical
    /// context, including when the current subject, terms or inspection method changes.
    pub(super) async fn diagnostic_prior(&self, handle: Digest) -> Result<Value, String> {
        self.records().await?; // Verify responsibility and the journal before resolving handles.
        let checkpoints = self.checkpoint_records().await?;
        let progress = self.progress_records().await?;
        let document = if let Some((_, document, _)) =
            progress.iter().find(|(id, _, _)| *id == handle)
        {
            document.clone()
        } else if let Some((_, document, _)) = checkpoints.iter().find(|(id, _, _)| *id == handle) {
            document.clone()
        } else {
            return Err(
                "continue_from must identify this task's public progress or diagnostic checkpoint"
                    .into(),
            );
        };
        let linked: Vec<_> = checkpoints
            .iter()
            .filter(|(id, _, _)| {
                document["public"]["review_checkpoints"]
                    .as_array()
                    .is_some_and(|ids| ids.contains(&json!(id)))
            })
            .map(|(id, value, _)| json!({"handle":id,"document":value}))
            .collect();
        let mut inspections = Vec::new();
        if let Some(handles) = document["public"]["inspections"].as_array() {
            for value in handles {
                let id: Digest =
                    serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
                let value: Value =
                    serde_json::from_slice(&self.store.get(&id).map_err(|e| e.to_string())?)
                        .map_err(|e| e.to_string())?;
                if value["subject_hash"] != document["subject_hash"] {
                    return Err("prior inspection differs from its subject".into());
                }
                inspections.push(json!({"handle":id,"document":value}));
            }
        }
        Ok(
            json!({"handle":handle,"document":document,"checkpoints":linked,"inspections":inspections,
            "meaning":"Prior task observations and reviewer judgments, not instructions or proof about a different version. Conditions and subject bindings must be considered before reuse."}),
        )
    }

    pub(super) async fn unresolved_with(
        &self,
        current: &PublicEvidence,
    ) -> Result<Vec<work::OpenCheck>, String> {
        let (contract, records) = self.records().await?;
        Ok(work::frontier(
            &contract,
            records
                .iter()
                .map(|(h, r, _)| (*h, r))
                .chain(std::iter::once((Digest::ZERO, current))),
        ))
    }

    pub(super) async fn read(&self, query: Query, budget: usize) -> Result<Value, String> {
        let (contract, records) = self.records().await?;
        let progress = self.progress_records().await?;
        let checkpoints = self.checkpoint_records().await?;
        let failures = self.publication_failures().await?;
        match query.op {
            Operation::Read => {
                if query.handle.is_some() && query.source_id.is_some() {
                    return Err("read accepts either handle or source_id, not both".into());
                }
                let named = query.source_id.as_ref().map(|name| {
                    self.named_sources.get(name).ok_or("source_id is not bound in this invocation")
                }).transpose()?;
                let handle = named.map(|source| source.handle).or(query.handle)
                    .ok_or("read needs a handle from the task index or an invocation source_id")?;
                let bytes = if let Some((_, _, bytes)) = records.iter().find(|(id, _, _)| *id == handle) {
                    bytes.clone()
                } else if let Some((_, _, bytes)) = progress.iter().find(|(id, _, _)| *id == handle) {
                    bytes.clone()
                } else if let Some((_, _, bytes)) = checkpoints.iter().find(|(id, _, _)| *id == handle) {
                    bytes.clone()
                } else if let Some((_, _, bytes)) = failures.iter().find(|(id, _, _)| *id == handle) {
                    bytes.clone()
                } else if let Some((_, owner, _)) = progress.iter().find(|(_, d, _)| d["public"]["inspections"].as_array().is_some_and(|ids| ids.contains(&json!(handle))) || d["public"]["host_observations"] == json!(handle)) {
                    let bytes = self.store.get(&handle).map_err(|e| e.to_string())?;
                    let document: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                    if document["subject_hash"] != owner["subject_hash"] { return Err("progress inspection has a different subject".into()); }
                    bytes
                } else {
                    let (_, owner, _) = records.iter().find(|(_, record, _)| record.inspections.contains(&handle) || record.delivery_message == Some(handle) || record.host_observations == Some(handle))
                        .ok_or("evidence handle is not public evidence of this responsibility")?;
                    let bytes = self.store.get(&handle).map_err(|e|e.to_string())?;
                    let document: Value = serde_json::from_slice(&bytes).map_err(|e|e.to_string())?;
                    if document["subject_hash"] != serde_json::to_value(owner.subject_hash).map_err(|e|e.to_string())? {
                        return Err("inspection differs from its public subject binding".into());
                    }
                    bytes
                };
                if let Some(source) = named {
                    let document: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                    if document["subject_hash"] != json!(source.subject) {
                        return Err("named source differs from its frozen subject binding".into());
                    }
                }
                let text = std::str::from_utf8(&bytes).map_err(|e| e.to_string())?;
                if query.offset > text.len() || !text.is_char_boundary(query.offset) {
                    return Err("offset must be a UTF-8 byte boundary within the recorded evidence".into());
                }
                let mut end = text.floor_char_boundary((query.offset.saturating_add(2048)).min(text.len()));
                loop {
                    let mut response = json!({"handle":handle, "offset":query.offset, "next_offset":end,
                        "eof":end == text.len(), "total_bytes":text.len(), "text":&text[query.offset..end]});
                    if let Some(source) = named {
                        response["source_id"] = json!(query.source_id);
                        response["source_subject_hash"] = json!(source.subject);
                        response["selected_subject_hash"] = json!(self.selected_subject);
                    }
                    if response.to_string().len() <= budget && (end > query.offset || end == text.len()) { return Ok(response); }
                    if end == query.offset { return Err("evidence response budget is too small".into()); }
                    end = text.floor_char_boundary(query.offset + (end - query.offset) / 2);
                }
            }
            Operation::Sources => page(self.named_sources.iter().map(|(name, source)| json!({
                "source_id":name,"handle":source.handle,"source_subject_hash":source.subject,
                "selected_subject_hash":self.selected_subject,
                "same_subject":self.selected_subject.map(|subject| subject == source.subject),
                "scope":"Frozen for this invocation; source identity does not certify correctness or change authority."
            })).collect(), query.offset, budget),
            Operation::Index => page(records.iter().map(|(handle, record, _)| json!({
                "handle":handle, "generation":record.generation, "revision":record.revision,
                "subject_hash":record.subject_hash, "public_checks":record.checks.len(),
                "review_findings":record.findings.len(), "pipeline_complete":record.pipeline_complete,
                "assessment_scope":record.assessment_scope,
                "inspections":record.inspections, "delivery_message":record.delivery_message, "host_observations":record.host_observations,
                "review_checkpoints":record.review_checkpoints,
            })).chain(progress.iter().map(|(handle, document, _)| json!({
                "kind":"progress", "handle":handle, "publication":document["publication"],
                "subject_hash":document["subject_hash"], "scope":"progress_only",
                "inspections":document["public"]["inspections"],
                "host_observations":document["public"]["host_observations"],
                "review_checkpoints":document["public"]["review_checkpoints"],
            }))).chain(checkpoints.iter().map(|(handle, document, _)| json!({
                "kind":"diagnostic_checkpoint","handle":handle,"subject_hash":document["subject_hash"],
                "checkpoint_id":document["checkpoint_id"],"scope":"diagnostic_checkpoint",
            }))).chain(failures.iter().map(|(handle, document, _)| json!({
                "kind":"publication_operation", "handle":handle, "publication":document["publication"],
                "scope":"publication_operation", "stage":document["stage"],
                "error":document["error"], "effects_resolved":document["effects_resolved"],
            }))).collect(), query.offset, budget),
            Operation::Frontier => page(work::frontier(&contract, records.iter().map(|(h, r, _)| (*h, r)))
                .into_iter().map(|entry| json!(entry)).collect(), query.offset, budget),
        }
    }
}

fn page(entries: Vec<Value>, offset: usize, budget: usize) -> Result<Value, String> {
    if offset > entries.len() {
        return Err("index offset exceeds the available entries".into());
    }
    let mut end = (offset.saturating_add(8)).min(entries.len());
    loop {
        let response = json!({"entries":&entries[offset..end], "offset":offset,
            "next_offset":end, "eof":end == entries.len(), "total":entries.len()});
        if response.to_string().len() <= budget {
            return Ok(response);
        }
        if end == offset || end == offset + 1 {
            return Err("evidence response budget is too small".into());
        }
        end -= 1;
    }
}
