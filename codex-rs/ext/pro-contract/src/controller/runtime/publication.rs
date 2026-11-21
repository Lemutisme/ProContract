//! Publication ownership and waiters. This is process control, not task acceptance.

use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::{Value, json};
use tokio::sync::watch;

use super::{Phase, ProgressAssessment, ThreadRuntime};
use crate::controller::succession_budget::now_ms;

#[derive(Clone, Serialize)]
pub(super) struct PublicationBinding {
    pub(super) request_id: String,
    pub(super) turn_id: String,
    pub(super) owner: Option<(u64, Option<u64>)>,
    pub(super) epoch: u64,
    pub(super) deadline_ms: u64,
    pub(super) assessment: ProgressAssessment,
}

struct Publication {
    binding: PublicationBinding,
    result: watch::Receiver<Option<Result<Value, String>>>,
}

impl Publication {
    fn is_live(&self) -> bool {
        self.result.borrow().is_none() && self.result.has_changed().is_ok()
    }
}

#[derive(Default)]
pub(super) struct Publications {
    pub(super) closed: bool,
    current: Option<Arc<Publication>>,
}

fn validate_request(request: &str) -> Result<(), String> {
    if request.is_empty() || request.len() > 128 {
        Err("publication request_id must contain 1..128 bytes".into())
    } else {
        Ok(())
    }
}

async fn wait_for_publication(publication: &Publication) -> Result<Value, String> {
    let mut result = publication.result.clone();
    loop {
        // A completed result remains readable after its original allocation.
        if let Some(outcome) = { result.borrow().clone() } {
            return outcome;
        }
        let remaining = publication.binding.deadline_ms.saturating_sub(now_ms()?);
        tokio::time::timeout(Duration::from_millis(remaining), result.changed())
            .await
            .map_err(|_| "publication's original action deadline elapsed; inspect progress_status; no work was replayed".to_string())?
            .map_err(|_| "publication owner stopped without a result; inspect retained effects before replay".to_string())?;
    }
}

impl ThreadRuntime {
    async fn recall_publication(
        &self,
        request: &str,
        options: &ProgressAssessment,
    ) -> Result<Option<Value>, String> {
        let grant = self.progress_grant().await?;
        let Some((_, handle)) = grant.completed.iter().find(|(id, _)| id == request) else {
            return Ok(None);
        };
        let stored: Value =
            serde_json::from_slice(&self.store.get(handle).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        let original: ProgressAssessment = if let Some(value) = stored.get("assessment_request") {
            serde_json::from_value(value.clone()).map_err(|e| e.to_string())?
        } else {
            ProgressAssessment {
                review: stored["diagnostic_review"].as_bool().unwrap_or(false),
                ..Default::default()
            }
        };
        if original != *options {
            return Err("request_id belongs to a different assessment; inspect progress_status for the original options".into());
        }
        Ok(Some(
            json!({"handle":handle,"reused":true,"scope":stored["scope"],
            "error":stored["error"],"effects_resolved":stored["effects_resolved"],
            "completion_certified":false,"read_with":"contract_read_evidence"}),
        ))
    }

    async fn publication_owner_current(
        &self,
        binding: &PublicationBinding,
        owner: Option<(u64, Option<u64>)>,
    ) -> Result<(), String> {
        if binding.owner != owner || self.state.lock().await.epoch != binding.epoch {
            return Err("publication belongs to a different executor epoch; status is readable but no work was replayed".into());
        }
        Ok(())
    }

    pub(crate) async fn publish_assessment(
        self: &Arc<Self>,
        request: String,
        turn: String,
        owner: Option<(u64, Option<u64>)>,
        options: ProgressAssessment,
    ) -> Result<Value, String> {
        validate_request(&request)?;
        options.validate()?;
        let mut registry = self.publications.lock().await;
        if let Some(receipt) = self.recall_publication(&request, &options).await? {
            return Ok(receipt);
        }
        if let Some(current) = registry.current.as_ref() {
            if current.binding.request_id == request {
                if current.binding.assessment != options {
                    return Err("request_id belongs to a different assessment; inspect progress_status for the original options".into());
                }
                if current.is_live() {
                    self.publication_owner_current(&current.binding, owner)
                        .await?;
                } else {
                    // A safe receipt may have reached the journal before the
                    // original owner lost its final grant update/acknowledgement.
                    self.recover_progress().await?;
                    if let Some(receipt) = self.recall_publication(&request, &options).await? {
                        return Ok(receipt);
                    }
                }
                let publication = Arc::clone(current);
                drop(registry);
                let mut value = wait_for_publication(&publication).await?;
                value["reused"] = json!(true);
                value["reattached"] = json!(true);
                return Ok(value);
            }
            if current.is_live() {
                return Err("another publication is still owned and running; inspect progress_status before new work".into());
            }
        }
        // No live owner may race this durable completion reconciliation.
        self.recover_progress().await?;
        if let Some(receipt) = self.recall_publication(&request, &options).await? {
            return Ok(receipt);
        }
        if registry.closed {
            return Err("publication admission is closed by thread teardown".into());
        }
        let settings = self
            .settings
            .progress
            .as_ref()
            .ok_or("progress publication is not authorized")?;
        let epoch = {
            let state = self.state.lock().await;
            let permitted = match owner {
                None => {
                    matches!(state.phase, Phase::Drafting | Phase::Working)
                        && state.last_idle.is_none()
                }
                Some((epoch, _)) => state.phase == Phase::Succeeding && state.epoch == epoch,
            };
            if !permitted {
                return Err(
                    "publication requires the current executor and original authority".into(),
                );
            }
            state.epoch
        };
        let seconds = self
            .action_seconds(
                owner.and_then(|(_, end)| end),
                options.assessment_secs.unwrap_or(settings.assessment_secs),
            )
            .await?;
        let binding = PublicationBinding {
            request_id: request,
            turn_id: turn,
            owner,
            epoch,
            deadline_ms: now_ms()?
                .saturating_add((seconds + super::progress::CLEANUP_SECONDS) * 1000),
            assessment: options,
        };
        let (send, receive) = watch::channel(None);
        let publication = Arc::new(Publication {
            binding: binding.clone(),
            result: receive,
        });
        registry.current = Some(Arc::clone(&publication));
        let runtime = Arc::clone(self);
        // Registration and insertion are atomic with respect to stop(). The
        // requesting transport owns only its receiver, never this operation.
        self.spawn(async move {
            let outcome = runtime.publish_assessment_owned(binding).await;
            send.send_replace(Some(outcome));
        });
        drop(registry);
        wait_for_publication(&publication).await
    }

    pub(super) async fn progress_ready(&self) -> Result<(), String> {
        let registry = self.publications.lock().await;
        if registry
            .current
            .as_ref()
            .is_some_and(|current| current.is_live())
        {
            return Err("publication is still owned and running; inspect progress_status or reattach the original request".into());
        }
        self.recover_progress().await
    }

    /// Handoff/failed-executor selection waits for a known existing operation,
    /// without admitting one or changing the original task/action allocation.
    pub(super) async fn drain_publication(&self, epoch: u64) -> Result<(), String> {
        let publication = self.publications.lock().await.current.clone();
        if let Some(publication) = publication.filter(|p| p.is_live()) {
            if publication.binding.epoch != epoch {
                return Err("a different executor epoch still owns a publication".into());
            }
            // The durable pending/complete binding, rather than a caller-level
            // result, determines whether effects are resolved for the handoff.
            let _outcome = wait_for_publication(&publication).await;
        }
        if self.state.lock().await.epoch != epoch {
            return Err("publication drain was superseded by another executor epoch".into());
        }
        self.progress_ready().await
    }

    /// Read-only operation status. Unknown ownership never becomes permission
    /// to clear pending, repeat effects, renew a grant or certify completion.
    pub(crate) async fn publication_status(
        &self,
        request: Option<String>,
        owner: Option<(u64, Option<u64>)>,
    ) -> Result<Value, String> {
        if let Some(request) = &request {
            validate_request(request)?;
        }
        let registry = self.publications.lock().await;
        // Observe the channel before querying the durable grant. If completion
        // races this query, its committed receipt wins below; an older pending
        // read can never be combined with a newer closed channel into a false
        // unknown-effects report.
        let observed = registry.current.as_ref().map(|current| {
            let result = current.result.borrow().clone();
            let live = result.is_none() && current.result.has_changed().is_ok();
            (Arc::clone(current), result, live)
        });
        let grant = self.progress_grant().await?;
        let requested = request
            .or_else(|| grant.pending.clone())
            .or_else(|| {
                registry
                    .current
                    .as_ref()
                    .map(|p| p.binding.request_id.clone())
            })
            .or_else(|| grant.completed.last().map(|(request, _)| request.clone()));
        let Some(request) = requested else {
            return Ok(json!({"state":"idle","completion_certified":false}));
        };
        if let Some((_, handle)) = grant.completed.iter().find(|(id, _)| id == &request) {
            let document: Value =
                serde_json::from_slice(&self.store.get(handle).map_err(|e| e.to_string())?)
                    .map_err(|e| e.to_string())?;
            return Ok(
                json!({"state":"completed","request_id":request,"handle":handle,
                "assessment_request":document["assessment_request"],"scope":document["scope"],
                "effects_resolved":document["effects_resolved"],"error":document["error"],
                "read_with":"contract_read_evidence","completion_certified":false}),
            );
        }
        if let Some((current, result, live)) =
            observed.filter(|(p, _, _)| p.binding.request_id == request)
        {
            let state = if live && registry.closed {
                "stopping"
            } else if live {
                "running"
            } else if grant.pending.as_ref() == Some(&request) {
                "unresolved"
            } else {
                "finished_without_receipt"
            };
            let reattach = live
                && !registry.closed
                && self
                    .publication_owner_current(&current.binding, owner)
                    .await
                    .is_ok();
            return Ok(json!({"state":state,"request_id":request,
                "turn_id":current.binding.turn_id,"execution_epoch":current.binding.epoch,
                "assessment_request":current.binding.assessment,"action_deadline_ms":current.binding.deadline_ms,
                "owner_known_live":live,"reattach_allowed":reattach,
                "result":result,"completion_certified":false}));
        }
        if grant.pending.as_ref() == Some(&request) {
            self.ledger
                .verify_experiment_chain()
                .await
                .map_err(|e| e.to_string())?;
            let reservation = self
                .ledger
                .experiments()
                .await
                .map_err(|e| e.to_string())?
                .into_iter()
                .rev()
                .find(|row| {
                    row.event.kind == codex_pro_contract_store::ExperimentKind::Execution
                        && row.event.body["phase"] == "progress_reserved"
                        && row.event.body["contract_id"] == json!(self.contract_id().0)
                        && row.event.body["request_id"] == json!(request)
                        && row.event.body["publication"] == json!(grant.used)
                })
                .map(|row| row.event.body);
            return Ok(
                json!({"state":"unresolved","request_id":request,"owner_known_live":false,
                "reservation":reservation,"reattach_allowed":false,"completion_certified":false}),
            );
        }
        Ok(json!({"state":"not_found","request_id":request,"completion_certified":false}))
    }
}
