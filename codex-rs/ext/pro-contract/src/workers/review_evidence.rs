//! Bounded access to the already-captured subject and frozen terms, with optional admitted
//! computation. The caller supplies identities; no model-controlled host paths are accepted.

use codex_protocol::dynamic_tools::DynamicToolCallOutputContentItem;
use codex_protocol::dynamic_tools::DynamicToolFunctionSpec;
use codex_protocol::dynamic_tools::DynamicToolResponse;
use codex_protocol::dynamic_tools::DynamicToolSpec;
use serde::Deserialize;
use serde_json::Value;
use serde_json::json;

use super::WorkerError;
use crate::BlobStore;
use crate::EntryKind;
use crate::Subject;
use crate::Terms;

#[path = "review_checkpoints.rs"]
pub(crate) mod checkpoints;

pub(crate) const TOOL: &str = "read_evidence";
pub(crate) const INSPECT_TOOL: &str = "inspect_evidence";
// Leave room for the runtime's remaining-budget field in the same JSON object.
const MAX_REPLY: usize = 8192 - 128;
const PAGE_BYTES: usize = 4096;
const MAX_READS: usize = 4096;
const MAX_FILE: u64 = 4 << 20;

/// Independently bound judgments plus local protocol failures. A rejected support
/// claim becomes an explicit gap; it cannot erase an unrelated observation or defeat.
#[derive(Debug)]
pub(crate) struct ResolvedReview {
    pub message: String,
    pub rejected: Vec<super::reviewer::MissingEvidence>,
}

impl ResolvedReview {
    pub(crate) fn protocol_error(&self) -> Option<String> {
        (!self.rejected.is_empty()).then(|| {
            self.rejected
                .iter()
                .map(|row| format!("{}: {}", row.requirement_id, row.reason))
                .collect::<Vec<_>>()
                .join("; ")
        })
    }
}

pub(crate) struct ReviewEvidence {
    subject: Subject,
    baseline: Option<Subject>,
    store: BlobStore,
    terms: Terms,
    documents: Vec<(String, String)>,
    delivery_message_complete: bool,
    projection: Option<crate::controller::capture_projection::BoundProjection>,
    order: Vec<usize>,
    baseline_order: Vec<usize>,
    issued: Vec<Value>,
    calls: usize,
    required_coverage: [Vec<bool>; 2],
    execution: Option<crate::inspection_grant::InspectionGrant>,
    computations: Vec<String>,
    retained_computations: std::collections::BTreeSet<String>,
    protocol_log: Option<(crate::Ledger, codex_pro_contract_store::Identities, Value)>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    op: String,
    source_id: String,
    offset: usize,
}

impl ReviewEvidence {
    pub(crate) fn new(
        subject: Subject,
        store: BlobStore,
        terms: &Terms,
        checks: String,
    ) -> Result<Self, WorkerError> {
        let order = source_order(&subject, terms);
        // A quote may justify several obligations/context entries. Transport it
        // once, with lossless references, rather than making each review reread
        // the same request through every derived row.
        let mut source_quotes = Vec::<String>::new();
        let mut quote_index = |text: &str| {
            source_quotes
                .iter()
                .position(|quote| quote == text)
                .unwrap_or_else(|| {
                    source_quotes.push(text.to_owned());
                    source_quotes.len() - 1
                })
        };
        let obligations: Vec<Value> = terms
            .obligations()
            .into_iter()
            .map(|row| {
                json!({
                    "id":row.id,"scope":row.scope,"text":row.text,"inferred":row.inferred,
                    "source_quote_index":quote_index(row.source_quote),
                })
            })
            .collect();
        let context: Vec<Value> = terms.context.iter().map(|row| json!({
            "kind":row.kind,"text":row.text,"source_quote_index":quote_index(&row.source_quote),
        })).collect();
        let encoded_terms = serde_json::to_string(&json!({
            "catalog_protocol":"scoped-obligations-v2",
            "terms_hash":crate::digest_of("terms", terms),
            "human_request":{"source_id":"human_request","bytes":terms.intake_text.len(),
                "sha256":codex_pro_contract::Digest::of(terms.intake_text.as_bytes())},
            "obligations":obligations,"source_quotes":source_quotes,
            "out_of_scope":terms.out_of_scope,"context":context,
            "meaning":"This catalog is derived from the frozen terms. It grants no new authority. requirement_id in the response schema refers to these exact obligation IDs."
        }))
            .map_err(|error| WorkerError::Malformed(error.to_string()))?;
        let required_coverage = [
            vec![false; terms.intake_text.len().min(MAX_FILE as usize)],
            vec![false; encoded_terms.len().min(MAX_FILE as usize)],
        ];
        Ok(Self {
            subject,
            baseline: None,
            store,
            terms: terms.clone(),
            order,
            baseline_order: Vec::new(),
            required_coverage,
            delivery_message_complete: false,
            projection: None,
            documents: vec![
                ("human_request".to_string(), terms.intake_text.clone()),
                ("contract_terms".to_string(), encoded_terms),
                ("check_receipts".to_string(), checks),
            ],
            issued: Vec::new(),
            calls: 0,
            execution: None,
            computations: Vec::new(),
            retained_computations: std::collections::BTreeSet::new(),
            protocol_log: None,
        })
    }

    pub(crate) fn with_host_observations(mut self, document: Value) -> Result<Self, WorkerError> {
        if document["kind"] != "host_observations_v1"
            || document["subject_hash"] != json!(self.subject.subject_hash)
            || document["terms_hash"] != json!(crate::digest_of("terms", &self.terms))
        {
            return Err(WorkerError::Malformed(
                "host observations differ from the assessment binding".into(),
            ));
        }
        if let Some(value) = document
            .get("capture_projection")
            .filter(|value| !value.is_null())
        {
            let projection: crate::controller::capture_projection::BoundProjection =
                serde_json::from_value(value.clone())
                    .map_err(|error| WorkerError::Malformed(error.to_string()))?;
            projection
                .validate(&self.subject)
                .map_err(WorkerError::Malformed)?;
            self.projection = Some(projection);
        }
        let text = document.to_string();
        if text.len() > MAX_FILE as usize {
            return Err(WorkerError::InputBudget(
                "host observations exceed the evidence capability".into(),
            ));
        }
        self.documents.push(("host_observations".into(), text));
        Ok(self)
    }

    pub(crate) fn with_protocol_log(
        mut self,
        ledger: crate::Ledger,
        identities: codex_pro_contract_store::Identities,
        binding: Value,
    ) -> Self {
        self.protocol_log = Some((ledger, identities, binding));
        self
    }

    pub(crate) fn with_diagnosis(mut self, question: Option<&str>) -> Self {
        self.documents.push(("diagnosis_request".into(), json!({
            "question":question,
            "meaning":"Requested focus for diagnosis, not a new obligation or acceptance criterion. Omitted obligations remain unassessed.",
        }).to_string()));
        self
    }

    /// Earlier assessments remain readable context, independently of whether this
    /// operation is diagnostic or formal. They cannot supply a current citation.
    pub(crate) fn with_prior_assessment(mut self, prior: Option<Value>) -> Self {
        if let Some(prior) = prior {
            let text = prior.to_string();
            let text = if text.len() > MAX_FILE as usize {
                json!({"availability":"unavailable", "reason":"Prior context exceeds this reader's source limit.",
                    "retained_handle":prior.get("handle"),"bytes":text.len(),
                    "sha256":codex_pro_contract::Digest::of(text.as_bytes()),
                    "meaning":"Historical context was not supplied; no conclusion about the current subject follows."
                }).to_string()
            } else {
                text
            };
            self.documents.push(("prior_assessment".into(), text));
        }
        self
    }

    pub(crate) async fn record_protocol_result(
        &self,
        attempt: u32,
        message: &str,
        result: &Result<ResolvedReview, WorkerError>,
    ) -> Result<(), WorkerError> {
        let Some((ledger, identities, binding)) = &self.protocol_log else {
            return Ok(());
        };
        let retained = if message.len() <= 65536 {
            message.to_string()
        } else {
            super::bounded_head_tail(message, 8192)
        };
        let handle = self
            .store
            .put(retained.as_bytes())
            .map_err(|e| WorkerError::Failed(e.to_string()))?;
        let resolved = result.as_ref().ok();
        let resolved_handle = resolved
            .map(|r| self.store.put(r.message.as_bytes()))
            .transpose()
            .map_err(|e| WorkerError::Failed(e.to_string()))?;
        let error = match result {
            Ok(value) => value.protocol_error(),
            Err(error) => Some(error.to_string()),
        };
        ledger.append_experiment(&codex_pro_contract_store::ExperimentEvent {
            kind: codex_pro_contract_store::ExperimentKind::Execution,
            identities: identities.clone(),
            body: json!({"phase":"review_protocol_result", "binding":binding,
                "subject_hash":self.subject.subject_hash,"terms_hash":crate::digest_of("terms", &self.terms),
                "attempt":attempt,"valid":error.is_none(),"raw_response":handle,
                "resolved_response":resolved_handle,
                "rejected_support":resolved.map(|r| &r.rejected),
                "original_sha256":codex_pro_contract::Digest::of(message.as_bytes()),
                "original_bytes":message.len(),"complete":message.len() <= 65536,
                "error":error.map(|e| super::bounded(&e, 2000))}),
        }).await.map_err(|e| WorkerError::Failed(format!("review protocol receipt unavailable: {e}")))?;
        Ok(())
    }

    pub(crate) async fn reserve_protocol_correction(
        &self,
        remaining: std::time::Duration,
    ) -> Result<(), WorkerError> {
        let Some((ledger, identities, binding)) = &self.protocol_log else {
            return Ok(());
        };
        ledger.append_experiment(&codex_pro_contract_store::ExperimentEvent {
            kind: codex_pro_contract_store::ExperimentKind::Execution,
            identities: identities.clone(),
            body: json!({"phase":"review_protocol_correction_reserved","binding":binding,
                "subject_hash":self.subject.subject_hash,"terms_hash":crate::digest_of("terms", &self.terms),
                "maximum_corrections":1,"remaining_millis":remaining.as_millis(),
                "allowances_renewed":false}),
        }).await.map_err(|e| WorkerError::Failed(format!("review correction reservation unavailable: {e}")))?;
        Ok(())
    }

    pub(crate) fn with_delivery_message(mut self, document: Value) -> Result<Self, WorkerError> {
        if document["kind"] != "native_final_response"
            || document["subject_hash"] != json!(self.subject.subject_hash)
            || !document["complete"].is_boolean()
        {
            return Err(WorkerError::Malformed(
                "delivery response differs from the frozen subject".into(),
            ));
        }
        self.delivery_message_complete = document["complete"] == true;
        self.documents
            .push(("delivery_message".into(), document.to_string()));
        Ok(self)
    }

    pub(crate) fn with_baseline(mut self, baseline: Subject) -> Self {
        self.baseline_order = source_order(&baseline, &self.terms);
        self.baseline = Some(baseline);
        self
    }

    pub(crate) fn with_execution(
        mut self,
        environment: crate::CheckEnvironment,
        grant: crate::ReviewExecution,
        directory: std::path::PathBuf,
        lifetime: std::time::Duration,
    ) -> Result<Self, WorkerError> {
        let grant = crate::inspection_grant::InspectionGrant::open(
            environment,
            grant,
            directory,
            self.subject.subject_hash,
            &self.terms,
            lifetime,
        )
        .map_err(WorkerError::Start)?;
        self.computations = grant.history(&self.store).map_err(WorkerError::Start)?;
        self.execution = Some(grant);
        Ok(self)
    }

    pub(crate) fn can_compute(&self) -> bool {
        self.execution.is_some()
    }

    /// Only the caller's frozen, host-published allowlist may supply receipts.
    /// The source ID is content-addressed across assessments; computation:N
    /// continues to name this assessment's grant history exclusively.
    pub(crate) fn with_retained_computations(
        mut self,
        handles: Result<Vec<codex_pro_contract::Digest>, String>,
        binding: Result<codex_pro_contract::Digest, String>,
    ) -> Self {
        let mut unavailable = Vec::new();
        match handles.and_then(|handles| binding.map(|binding| (handles, binding))) {
            Ok((handles, binding)) => {
                for handle in handles {
                    let result: Result<String, String> = (|| {
                        let bytes = self.store.get(&handle).map_err(|e| e.to_string())?;
                        if codex_pro_contract::Digest::of(&bytes) != handle {
                            return Err("retained computation digest mismatch".into());
                        }
                        if bytes.len() > MAX_FILE as usize {
                            return Err("retained computation exceeds read capability".into());
                        }
                        let document: Value =
                            serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                        if document["subject_hash"] != json!(self.subject.subject_hash)
                            || document["grant_binding"] != json!(binding)
                        {
                            return Err("retained computation has different subject, terms, inputs or computation conditions".into());
                        }
                        self.validate_computation_sources(&document)?;
                        String::from_utf8(bytes).map_err(|e| e.to_string())
                    })();
                    match result {
                        Ok(text) => {
                            let source = format!("receipt:{handle}");
                            if self.retained_computations.insert(source.clone()) {
                                self.documents.push((source, text));
                            }
                        }
                        Err(reason) => unavailable.push(json!({"sha256":handle,"reason":reason})),
                    }
                }
            }
            Err(reason) => unavailable.push(json!({"reason":reason})),
        }
        // Historical access failures do not disable current checks or renew a
        // grant. Preserve the failure as context, never as positive coverage.
        if !unavailable.is_empty() {
            self.documents.push((
                "retained_computation_availability".into(),
                json!({"unavailable":unavailable}).to_string(),
            ));
        }
        self
    }

    fn validate_computation_sources(&self, document: &Value) -> Result<(), String> {
        let named_layout = document["schema"] == 3;
        let versioned = named_layout || document["schema"] == 2;
        if !versioned && !document["schema"].is_null() && document["schema"] != 1 {
            return Err("unsupported computation receipt schema".into());
        }
        if (named_layout && document["source_layout"] != crate::inspection::SOURCE_LAYOUT)
            || (!named_layout && document.get("source_layout").is_some())
        {
            return Err("computation source layout does not match its receipt schema".into());
        }
        let inputs = match document.get("inputs") {
            Some(Value::Array(inputs)) => inputs.as_slice(),
            None | Some(Value::Null) if !versioned => &[],
            _ => return Err("computation input identities are unavailable".into()),
        };
        let mut paths = Vec::new();
        for input in inputs {
            let id = input["source_id"]
                .as_str()
                .ok_or("computation input source ID is missing")?;
            if !versioned && !id.starts_with("file:") {
                return Err("legacy computation cannot attest an original-version input".into());
            }
            let (source, index) = self.file_source(id)?;
            let EntryKind::File { len, digest, .. } = &source.manifest.entries[index].kind else {
                return Err("computation source is not a frozen file".into());
            };
            if (versioned && input["source_subject_hash"] != json!(source.subject_hash))
                || input["sha256"] != json!(digest)
                || input["bytes"] != json!(len)
                || input["path"] != json!(format!("/evidence/{}", id.replace(':', "-")))
            {
                return Err(
                    "computation input differs from the offered source version or bytes".into(),
                );
            }
            if named_layout {
                let captured_path = &source.manifest.entries[index].path;
                let expected = crate::inspection::source_paths(id, captured_path)?;
                if input["captured_path"] != json!(captured_path)
                    || input["named_path"] != json!(expected.named)
                {
                    return Err(
                        "computation layout differs from the offered captured source path".into(),
                    );
                }
                paths.push(expected);
            } else if input.get("named_path").is_some() || input.get("captured_path").is_some() {
                return Err("legacy computation cannot attest a named source layout".into());
            }
        }
        crate::inspection::validate_paths(&paths)?;
        Ok(())
    }

    /// Facts from this instantiated reader and its opened grant, not from a desired profile.
    /// Tool availability does not establish that a subsequent execution will be admitted or succeed.
    pub(crate) fn capabilities(&self) -> Value {
        let prior = self
            .documents
            .iter()
            .find(|(id, _)| id == "prior_assessment")
            .map(|(_, text)| {
                let document: Value = serde_json::from_str(text).unwrap_or(Value::Null);
                let subject = document
                    .pointer("/document/subject_hash")
                    .or_else(|| document.get("subject_hash"))
                    .and_then(|value| {
                        serde_json::from_value::<codex_pro_contract::Digest>(value.clone()).ok()
                    });
                json!({"source_id":"prior_assessment", "subject_hash":subject,
                    "same_subject":subject.map(|hash| hash == self.subject.subject_hash),
                    "current_support_citation":false})
            });
        json!({
            "subject_hash":self.subject.subject_hash,
            "terms_hash":crate::digest_of("terms", &self.terms),
            "read_evidence":{"encoding":"utf8_text", "image_rendering":false},
            "host_observations":self.documents.iter().any(|(id, _)| id == "host_observations"),
            "inspect_evidence":self.execution.as_ref().map(|grant| json!({
                "tool_installed":true,"effective_image":grant.environment.image,
                "total_call_limit":grant.limits.max_calls,"timeout_secs":grant.limits.timeout_secs,
                "max_output_bytes":grant.limits.max_output_bytes,
                "source_layout":crate::inspection::SOURCE_LAYOUT,
                "selected_source_manifest":crate::inspection::SOURCE_MANIFEST,
                "resources":grant.limits.resources.clone().unwrap_or_default(),
                "admission":"Original remaining time, reservations and unresolved effects checked per call; execution success unknown."
            })),
            "record_review":self.can_checkpoint(),
            "retained_computation_sources":self.computations.len(),
            "archived_computation_sources":self.retained_computations.len(),
            "baseline_sources":self.baseline.as_ref().map(|baseline| json!({
                "subject_hash":baseline.subject_hash,"entries":baseline.manifest.entries.len(),
                "catalog_source_id":"baseline",
            })),
            "prior_assessment":prior,
        })
    }

    pub(crate) fn inspection_tool(&self) -> DynamicToolSpec {
        let obligations: Vec<String> = self.terms.obligations().into_iter().map(|r| r.id).collect();
        DynamicToolSpec::Function(DynamicToolFunctionSpec {
            name: INSPECT_TOOL.into(),
            description: "Run bounded Python over selected frozen files and declared inputs in a network-less, read-only container with writable /tmp. Catalog named_inspection_path preserves captured names and relative layout under /sources/current or /sources/baseline. Only selected files are present, not the original workspace/environment; symlinks are not followed. /sources/manifest.json gives complete selected paths. Legacy aliases remain /evidence/file-N and /evidence/baseline-N. Receipts bind source subjects, bytes and layout. Declared inputs use host_observations paths. Return a computation source_id readable with read_evidence. Computations establish only observed scope.".into(),
            input_schema: super::strict_object(json!({
                "source_ids":{"type":"array","items":{"type":"string","pattern":"^(file|baseline):(0|[1-9][0-9]*)$"},"maxItems":32},
                "requirements":{"type":"array","items":{"type":"string","enum":obligations},"minItems":1},
                "python":{"type":"string","maxLength":32768},
            })), defer_loading:false,
        })
    }

    pub(crate) async fn inspect(
        &mut self,
        arguments: Value,
        remaining: std::time::Duration,
    ) -> DynamicToolResponse {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Compute {
            source_ids: Vec<String>,
            requirements: Vec<String>,
            python: String,
        }
        let result: Result<String, String> = async {
            let grant = self.execution.clone().ok_or("no computation grant")?;
            let request: Compute = serde_json::from_value(arguments.clone()).map_err(|e| e.to_string())?;
            if request.source_ids.len() > 32 || request.python.len() > 32768 || request.requirements.is_empty()
                || request.requirements.iter().any(|id| !self.terms.obligations().iter().any(|r| &r.id == id)) {
                return Err("computation needs nonempty catalog obligation IDs, at most 32 frozen file IDs and at most 32768 Python bytes".into());
            }
            let mut sources = Vec::new();
            let mut paths = Vec::new();
            let mut seen = std::collections::BTreeSet::new();
            let mut total = 0usize;
            for id in &request.source_ids {
                if !seen.insert(id) { return Err("duplicate computation source".into()); }
                let bytes = self.file_bytes(id, 64 << 20)?;
                total = total.saturating_add(bytes.len());
                if total > 128 << 20 { return Err("computation input exceeds 128 MiB".into()); }
                let (subject, index) = self.file_source(id)?;
                let captured_path = subject.manifest.entries[index].path.clone();
                paths.push(crate::inspection::source_paths(id, &captured_path)?);
                sources.push(crate::inspection::Source {
                    id:id.clone(), bytes, subject:subject.subject_hash, captured_path,
                });
            }
            crate::inspection::validate_paths(&paths)?;
            // Invalid input caused no execution. Reserve only the admitted action,
            // before dispatch; every dispatched failure or unknown effect stays spent.
            let (work, remaining) = grant.reserve(&arguments, remaining)?;
            let value = crate::inspection::compute(&grant.environment, &grant.limits, &work,
                crate::inspection::Computation { subject:self.subject.subject_hash, requirements:&request.requirements,
                    sources:&sources, python:&request.python, remaining }).await?;
            let source_id = format!("computation:{}", self.computations.len());
            let document = grant.retain(&work, value.clone(), &self.store)?;
            let digest = self.store.put(document.as_bytes()).map_err(|e| e.to_string())?;
            let state = value["execution"]["state"].clone();
            let shutdown_confirmed = value["execution"]["shutdown_confirmed"].clone();
            // Unknown effects stop further computations. The existing read-only records remain
            // available; no inference is made that an interrupted action did not happen.
            self.computations.push(document);
            Ok(json!({"source_id":source_id,"sha256":digest,"execution_state":state,
                "shutdown_confirmed":shutdown_confirmed,"output_truncated":value["output_truncated"],
                "instruction":"Read this computation source before citing it. Failed or truncated computations cannot supply positive coverage."}).to_string())
        }.await;
        let (text, success) = match result {
            Ok(text) => (text, true),
            Err(reason) => (format!("Refused: {}", super::bounded(&reason, 700)), false),
        };
        DynamicToolResponse {
            structured_content: None,
            content_items: vec![DynamicToolCallOutputContentItem::InputText { text }],
            success,
        }
    }

    fn file_source(&self, id: &str) -> Result<(&Subject, usize), String> {
        let (kind, number) = id.split_once(':').ok_or("invalid source ID")?;
        let index = number.parse::<usize>().map_err(|_| "invalid source ID")?;
        if id != format!("{kind}:{index}") {
            return Err("noncanonical source ID".into());
        }
        let subject = match kind {
            "file" => &self.subject,
            "baseline" => self
                .baseline
                .as_ref()
                .ok_or("original task snapshot unavailable")?,
            _ => return Err("unknown source kind".into()),
        };
        subject
            .manifest
            .entries
            .get(index)
            .ok_or("unknown source")?;
        Ok((subject, index))
    }

    fn file_bytes(&self, id: &str, maximum: u64) -> Result<Vec<u8>, String> {
        let (subject, index) = self.file_source(id)?;
        let entry = &subject.manifest.entries[index];
        let EntryKind::File { len, digest, .. } = &entry.kind else {
            return Err("links are not followed".into());
        };
        if *len > maximum {
            return Err("source exceeds capability limit".into());
        }
        let bytes = self.store.get(digest).map_err(|e| e.to_string())?;
        if bytes.len() as u64 != *len || codex_pro_contract::Digest::of(&bytes) != *digest {
            return Err("frozen source identity mismatch".into());
        }
        Ok(bytes)
    }

    pub(crate) fn prompt(&self, instructions: &str) -> String {
        let diagnosis = if self.can_checkpoint() {
            "This is a diagnostic assessment. diagnosis_request gives its focus; record_review persists checkpoints. Neither grants acceptance."
        } else {
            ""
        };
        let history = if self
            .documents
            .iter()
            .any(|(id, _)| id == "prior_assessment")
        {
            "prior_assessment retains its recorded subject, conditions and method; it supplies no current support citation."
        } else {
            ""
        };
        let computation = if self.can_compute() {
            "inspect_evidence runs bounded Python on frozen current/baseline files and declared inputs; read results through read_evidence."
        } else {
            "No inspect_evidence tool; binary/image content is unreadable here."
        };
        let retained = if self.retained_computations.is_empty() {
            ""
        } else {
            "receipt:<sha256> sources are original host-linked computations with matching subject, terms, inputs and computation conditions. Read and assess their observed scope independently; their prior review verdict is not current support. Reading them spends no computation reservation."
        };
        let execution_sources = if self.projection.is_some() {
            "Execution support needs scoped host_observations, check_receipts, or sources whose producer-declared origin.kind is execution_observation. Recorded time/completion/scope apply; a running call is not completion. Artifacts/computations cannot attest past executor actions."
        } else {
            "Execution support needs scoped host_observations or check_receipts; artifacts/computations cannot attest past executor actions."
        };
        format!(
            "{}\n\n{diagnosis}\n\
read_evidence returns objects in code mode: list source_id=\"\", offset=0; read returned IDs and follow next_offset to eof. \
List source_id=\"baseline\" for original files; their versioned pages support comparison, not current-outcome or execution certification. \
Read human_request and contract_terms; source_quote_index indexes source_quotes. Response requirement_id uses catalog IDs. \
Supported evidence needs a host-issued citation_id. \
{history} \
{retained} \
{execution_sources} \
response_capture.state=available attests delivery, not correctness; progress is not final delivery. \
No host shell/network/image rendering/arbitrary paths. {computation}\n\nRuntime evidence capabilities: {}",
            instructions,
            self.capabilities(),
        )
    }

    pub(crate) fn tool() -> DynamicToolSpec {
        DynamicToolSpec::Function(DynamicToolFunctionSpec {
            name: TOOL.to_string(),
            description: "Read frozen review evidence only. list: source_id empty for current/context/computation sources or baseline for original captured files; offset is cursor. index is the single-entry operation. read: source_id from a catalog, offset is next_offset or zero. Each response <=8192 bytes; text pages up to 4096 UTF-8 bytes. Follow next_offset. Original files retain their source subject and are comparison context, not current support citations. Candidate entry equality is byte/mode identity, not semantic equivalence. delivery_message is communication, not proof of its claims.".to_string(),
            input_schema: super::strict_object(json!({
                "op":{"type":"string","enum":["list","index","read"]},
                "source_id":{"type":"string"}, "offset":{"type":"integer"}
            })),
            defer_loading: false,
        })
    }

    pub(crate) fn respond(&mut self, arguments: Value) -> DynamicToolResponse {
        let request = serde_json::from_value::<Request>(arguments);
        let result = request
            .map_err(|error| error.to_string())
            .and_then(|request| {
                if self.calls >= MAX_READS {
                    Err("review evidence call budget exhausted".to_string())
                } else {
                    self.read(request)
                }
            });
        // Rejected arguments delivered no evidence. They do not exhaust the page
        // allowance; valid reads still count and the original action deadline applies.
        if result.is_ok() {
            self.calls += 1;
        }
        let (text, success) = match result {
            Ok(text) if text.len() <= MAX_REPLY => (text, true),
            Ok(_) => (
                "Refused: response exceeds evidence page budget.".to_string(),
                false,
            ),
            Err(reason) => (format!("Refused: {}", super::bounded(&reason, 700)), false),
        };
        DynamicToolResponse {
            structured_content: None,
            content_items: vec![DynamicToolCallOutputContentItem::InputText { text }],
            success,
        }
    }

    fn baseline_entry(&self, position: usize) -> Value {
        let Some(baseline) = &self.baseline else {
            return Value::Null;
        };
        let Some(index) = self.baseline_order.get(position) else {
            return Value::Null;
        };
        let entry = &baseline.manifest.entries[*index];
        json!({"source_id":format!("baseline:{index}"),"kind":"baseline",
            "path":super::bounded(&entry.path, 96),"captured_entry":entry.kind,
            "source_subject_hash":baseline.subject_hash,"assessed_subject_hash":self.subject.subject_hash,
            "current_support_citation":false,
            "inspection_path":(self.can_compute() && matches!(entry.kind, EntryKind::File { .. }))
                .then(|| format!("/evidence/baseline-{index}")),
            "named_inspection_path":self.named_inspection_path(&format!("baseline:{index}"), entry)})
    }

    fn named_inspection_path(&self, id: &str, entry: &crate::Entry) -> Option<String> {
        if !self.can_compute() || !matches!(entry.kind, EntryKind::File { .. }) {
            return None;
        }
        let path = crate::inspection::source_paths(id, &entry.path).ok()?.named;
        // Never advertise a truncated path as executable. Complete paths, including
        // unusually long ones, remain available in the selected-source manifest.
        (json!(path).to_string().len() <= 1024).then_some(path)
    }

    fn read(&mut self, request: Request) -> Result<String, String> {
        let Request {
            op,
            source_id,
            offset,
        } = request;
        if (op == "index" || op == "list") && (source_id.is_empty() || source_id == "baseline") {
            let baseline_only = source_id == "baseline";
            if baseline_only && self.baseline.is_none() {
                return Err("original task snapshot unavailable".into());
            }
            let file_end = self.documents.len() + self.subject.manifest.entries.len();
            let count = if baseline_only {
                self.baseline_order.len()
            } else {
                file_end + self.computations.len()
            };
            if offset > count {
                return Err("index outside catalog".into());
            }
            let entry = |cursor: usize| {
                if baseline_only {
                    self.baseline_entry(cursor)
                } else if cursor < self.documents.len() {
                    let (name, text) = &self.documents[cursor];
                    let kind = if self.retained_computations.contains(name) {
                        "computation_receipt"
                    } else {
                        "frozen_context"
                    };
                    json!({"source_id": name, "path": name, "bytes": text.len(), "kind":kind})
                } else if let Some(index) = self.order.get(cursor - self.documents.len()) {
                    let entry = &self.subject.manifest.entries[*index];
                    let origin = self
                        .projection
                        .as_ref()
                        .and_then(|bound| bound.projection.observation(&entry.path));
                    let task_path = self
                        .projection
                        .as_ref()
                        .and_then(|bound| bound.projection.task_path(&entry.path));
                    let kind = if origin.is_some() {
                        "host_observation"
                    } else if self.projection.is_none() || task_path.is_some() {
                        "candidate"
                    } else {
                        "unmapped_capture"
                    };
                    let mut description = json!({"source_id": format!("file:{index}"),
                        "path":super::bounded(task_path.as_deref().unwrap_or(&entry.path), /*cap*/ 96), "kind":kind,
                        "captured_entry":entry.kind,
                    "inspection_path":(self.can_compute() && matches!(entry.kind, EntryKind::File { .. }))
                        .then(|| format!("/evidence/file-{index}")),
                        "named_inspection_path":self.named_inspection_path(&format!("file:{index}"), entry),
                        "baseline_subject":self.baseline.as_ref().map(|b| b.subject_hash),
                        "matches_original_entry":if kind == "candidate" { self.baseline.as_ref().map(|b| b.manifest.entries.iter().any(|original| original == entry)) } else { None },
                    });
                    if let Some(projection) = &self.projection {
                        description["captured_path"] = json!(super::bounded(&entry.path, 96));
                        if let Some(source) = origin {
                            description["origin"] = json!({"kind":source.kind,
                                "sha256":source.sha256,"scope":super::bounded(&source.scope, 512),
                                "full_scope_source":"host_observations"});
                            description["producer"] = json!(projection.producer);
                        }
                    }
                    description
                } else if cursor >= file_end && cursor < count {
                    let index = cursor - file_end;
                    json!({"source_id":format!("computation:{index}"),"kind":"computation","bytes":self.computations[index].len()})
                } else {
                    Value::Null
                }
            };
            if op == "index" {
                return Ok(json!({"entry":entry(offset),"next_offset":(offset + 1).min(count),"eof":offset >= count.saturating_sub(1)}).to_string());
            }
            let mut entries = Vec::new();
            let mut cursor = offset;
            let page = |entries: &[Value], cursor| {
                json!({"entries":entries,"next_offset":cursor,"eof":cursor == count}).to_string()
            };
            while cursor < count {
                entries.push(entry(cursor));
                if page(&entries, cursor + 1).len() > MAX_REPLY {
                    entries.pop();
                    if entries.is_empty() {
                        return Err("catalog entry exceeds response budget".into());
                    }
                    break;
                }
                cursor += 1;
            }
            return Ok(page(&entries, cursor));
        }
        if op != "read" {
            return Err("unknown operation".to_string());
        }
        let baseline_source = source_id.starts_with("baseline:");
        let source_subject = if baseline_source {
            Some(self.file_source(&source_id)?.0.subject_hash)
        } else {
            None
        };
        let bytes =
            if let Some((_, text)) = self.documents.iter().find(|(name, _)| *name == source_id) {
                text.as_bytes().to_vec()
            } else if let Some(index) = source_id
                .strip_prefix("computation:")
                .and_then(|n| n.parse::<usize>().ok())
            {
                if source_id != format!("computation:{index}") {
                    return Err("noncanonical computation ID".into());
                }
                self.computations
                    .get(index)
                    .ok_or("unknown computation")?
                    .as_bytes()
                    .to_vec()
            } else {
                self.file_bytes(&source_id, MAX_FILE)?
            };
        if bytes.len() as u64 > MAX_FILE {
            return Err("source exceeds budget".to_string());
        }
        let text = std::str::from_utf8(&bytes).map_err(|error| error.to_string())?;
        if !text.is_char_boundary(offset) {
            return Err("invalid UTF-8 offset".to_string());
        }
        let mut end = text.floor_char_boundary((offset + PAGE_BYTES).min(text.len()));
        loop {
            // An original version is readable comparison context, not a claim
            // that the current artifact is correct or a past action occurred.
            let handle =
                (end > offset && !baseline_source).then(|| format!("e{}", self.issued.len()));
            let mut response = json!({"source_id":source_id,"offset":offset,"next_offset":end,
                "eof":end == text.len(),"text":&text[offset..end],"citation_id":handle});
            if baseline_source {
                response["source_subject_hash"] = json!(source_subject);
                response["assessed_subject_hash"] = json!(self.subject.subject_hash);
            }
            let response = response.to_string();
            if response.len() <= MAX_REPLY {
                if let Some(index) = self.documents[..2]
                    .iter()
                    .position(|(name, _)| *name == source_id)
                {
                    self.required_coverage[index][offset..end].fill(true);
                }
                if handle.is_some() {
                    self.issued.push(json!({"source_id":source_id,"sha256":codex_pro_contract::Digest::of(&bytes),
                        "start":offset,"end":end,"quote":&text[offset..end]}));
                }
                return Ok(response);
            }
            if end == offset {
                return Err("response exceeds budget".to_string());
            }
            end = text.floor_char_boundary(end - 1);
        }
    }

    /// Resolve each positive claim independently. Syntax, duplicate identities and
    /// contradictory judgments still reject the envelope; a bad citation affects only
    /// its obligation. Neither a guessed identifier nor an invalid source is accepted.
    pub(crate) fn resolve_review(&self, message: &str) -> Result<ResolvedReview, WorkerError> {
        if message.len() > 64 << 10 {
            return Err(WorkerError::InputBudget(
                "review response exceeds 64 KiB".into(),
            ));
        }
        super::reviewer::parse(message, &self.terms)?;
        let mut result = super::reviewer::normalize(message)?;
        let coverage = std::mem::take(
            result["coverage"]
                .as_array_mut()
                .ok_or_else(|| WorkerError::Malformed("review coverage is not an array".into()))?,
        );
        let mut rejected = Vec::new();
        let mut accepted = Vec::new();
        let mut citations = std::collections::BTreeMap::new();
        for mut row in coverage {
            let id = row["requirement_id"].as_str().ok_or_else(|| {
                WorkerError::Malformed("review coverage has no requirement ID".into())
            })?;
            let handle = row["evidence"].as_str().ok_or_else(|| {
                WorkerError::Malformed("review coverage has no citation ID".into())
            })?;
            match self.resolve_citation(id, handle) {
                Ok(citation) => {
                    let key = crate::digest_of("review_citation", &citation).to_string();
                    row["evidence"] = json!(key);
                    citations.entry(key).or_insert(citation);
                    accepted.push(row);
                }
                Err(reason) => rejected.push(super::reviewer::MissingEvidence {
                    requirement_id: id.to_owned(),
                    reason: format!(
                        "Support citation {:?} rejected: {reason}",
                        super::bounded(handle, 96)
                    ),
                }),
            }
        }
        result["coverage"] = json!(accepted);
        // Replace any model-supplied table. Only issued, scope-checked citations
        // can enter the host-owned representation, once per distinct bound range.
        result["citations"] = json!(citations);
        if !rejected.is_empty() {
            result["verdict"] = json!("partial");
            result["gaps"]
                .as_array_mut()
                .ok_or_else(|| WorkerError::Malformed("review gaps are not an array".into()))?
                .extend(rejected.iter().map(|row| json!(row)));
        }
        let resolved = result.to_string();
        // Revalidate the composed result before exposing it to any controller.
        // Every rejected claim is now a gap, so it cannot contribute to Support.
        super::reviewer::parse(&resolved, &self.terms)?;
        // The 64 KiB limit applies to the untrusted model response above. Host
        // evidence is bounded by the existing read capability, then paged from
        // the retained review; rendering it must not invalidate accepted claims.
        Ok(ResolvedReview {
            message: resolved,
            rejected,
        })
    }

    fn resolve_citation(
        &self,
        requirement_id: &str,
        handle: &str,
    ) -> Result<super::reviewer::Citation, &'static str> {
        if self
            .required_coverage
            .iter()
            .enumerate()
            .any(|(index, seen)| {
                seen.len() != self.documents[index].1.len() || seen.iter().any(|seen| !seen)
            })
        {
            return Err("review did not read the complete request and terms");
        }
        let index = handle
            .strip_prefix('e')
            .and_then(|id| id.parse::<usize>().ok())
            .ok_or("expected one issued citation_id, for example e12")?;
        if handle != format!("e{index}") {
            return Err("noncanonical citation_id");
        }
        let citation = self.issued.get(index).ok_or("citation_id was not issued")?;
        let source = citation["source_id"]
            .as_str()
            .ok_or("citation has no source")?;
        let execution_obligation = self
            .terms
            .obligations()
            .iter()
            .any(|o| requirement_id == o.id && o.scope == crate::ObligationScope::Execution);
        let scoped_execution_source = source
            .strip_prefix("file:")
            .and_then(|index| index.parse::<usize>().ok())
            .filter(|index| source == format!("file:{index}"))
            .and_then(|index| self.subject.manifest.entries.get(index))
            .and_then(|entry| {
                self.projection
                    .as_ref()
                    .and_then(|bound| bound.projection.observation(&entry.path))
            })
            .is_some_and(|observation| {
                observation.kind
                    == crate::controller::capture_projection::ObservationKind::ExecutionObservation
            });
        if execution_obligation
            && !matches!(source, "host_observations" | "check_receipts")
            && !scoped_execution_source
        {
            return Err(
                "execution obligation needs a scoped host observation or check receipt, not an artifact claim",
            );
        }
        if source.starts_with("computation:") || self.retained_computations.contains(source) {
            let text = if self.retained_computations.contains(source) {
                &self
                    .documents
                    .iter()
                    .find(|(name, _)| name == source)
                    .ok_or("retained computation was not issued")?
                    .1
            } else {
                let index = source
                    .strip_prefix("computation:")
                    .and_then(|n| n.parse::<usize>().ok())
                    .ok_or("invalid computation ID")?;
                self.computations
                    .get(index)
                    .ok_or("computation was not issued")?
            };
            let computation: Value =
                serde_json::from_str(text).map_err(|_| "invalid computation record")?;
            if computation["subject_hash"] != json!(self.subject.subject_hash) {
                return Err("computation belongs to another frozen subject");
            }
            if computation["execution"]["state"] != "completed"
                || computation["execution"]["shutdown_confirmed"] != true
                || computation["execution"]["truncated"] != false
                || computation["output_truncated"] != false
            {
                return Err("computation is incomplete, truncated, or its shutdown is unconfirmed");
            }
            // Query intent is not an access boundary. Relevance to another obligation
            // in the same frozen terms remains the reviewer's judgment.
        } else if source == "delivery_message" {
            if !self.delivery_message_complete {
                return Err("delivery response content is incomplete");
            }
        } else if !source.starts_with("file:")
            && source != "check_receipts"
            && source != "host_observations"
        {
            return Err("source is request/catalog context, not observed evidence");
        }
        let mut bound = citation.clone();
        bound["subject_hash"] = json!(self.subject.subject_hash);
        bound["terms_hash"] = json!(crate::digest_of("terms", &self.terms));
        serde_json::from_value(bound).map_err(|_| "invalid issued citation")
    }
}

fn source_order(subject: &Subject, terms: &Terms) -> Vec<usize> {
    let mentioned = crate::controller::views::mentioned_paths(&terms.requirements);
    let mut order: Vec<_> = (0..subject.manifest.entries.len()).collect();
    order.sort_by_key(|index| {
        let path = &subject.manifest.entries[*index].path;
        let required = mentioned.contains(path.as_str())
            || mentioned.contains(path.rsplit('/').next().unwrap_or(path));
        (
            !required,
            crate::controller::views::priority(
                path,
                crate::controller::views::ViewOrder::SourcesFirst,
            ),
            *index,
        )
    });
    order
}

#[cfg(test)]
#[path = "review_evidence_tests.rs"]
mod tests;
