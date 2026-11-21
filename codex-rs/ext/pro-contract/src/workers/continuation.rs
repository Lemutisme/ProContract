//! Context-level work selection. It grants no authority and changes no verdict.

use codex_pro_contract::Digest;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::WorkerError;

pub(crate) const DEFAULT_INSTRUCTIONS: &str = include_str!("../../policies/continuation.md");

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Action {
    Check,
    Diagnose,
    Verify,
    Resume,
    Successor,
    Rest,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Choice {
    Check {
        reason: String,
    },
    Diagnose {
        question: String,
    },
    Verify {
        reason: String,
    },
    Resume {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        requirements: Vec<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        request_admission: Option<Digest>,
        next_step: String,
    },
    Successor {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        requirements: Vec<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        request_admission: Option<Digest>,
        next_step: String,
    },
    Rest {
        reason: String,
    },
}

impl Choice {
    pub(crate) fn action(&self) -> Action {
        match self {
            Self::Check { .. } => Action::Check,
            Self::Diagnose { .. } => Action::Diagnose,
            Self::Verify { .. } => Action::Verify,
            Self::Resume { .. } => Action::Resume,
            Self::Successor { .. } => Action::Successor,
            Self::Rest { .. } => Action::Rest,
        }
    }
}

pub(crate) fn schema(
    actions: &[Action],
    requirements: &[String],
    request_admission: Option<Digest>,
) -> Value {
    // A nested union is supported by structured-output providers. Rest has no
    // work targets; the schema cannot request a combination the parser rejects.
    let mut variants: Vec<_> = actions.iter().filter(|action| {
        !matches!(action, Action::Resume | Action::Successor) || !requirements.is_empty()
    }).map(|action| {
        if matches!(action, Action::Rest | Action::Check | Action::Verify) {
            super::strict_object(json!({
                "action":{"type":"string","enum":[action]},
                "reason":{"type":"string","minLength":1,"description":"Why this offered operation is useful, or why work should rest. Selecting an operation is not acceptance."},
            }))
        } else if *action == Action::Diagnose {
            super::strict_object(json!({
                "action":{"type":"string","enum":[action]},
                "question":{"type":"string","minLength":1,"description":"The concrete uncertainty to investigate on the frozen artifact. This requests scoped diagnosis, not whole-task acceptance."},
            }))
        } else {
            super::strict_object(json!({
                "action":{"type":"string","enum":[action]},
                "requirements":{"type":"array","minItems":1,"items":{"type":"string","enum":requirements}},
                "next_step":{"type":"string","minLength":1,"description":"Concrete work for the executor using its offered capabilities. A plan, not evidence of completion."},
            }))
        }
    }).collect();
    if let Some(admission) = request_admission {
        for action in actions
            .iter()
            .filter(|action| matches!(action, Action::Resume | Action::Successor))
        {
            variants.push(super::strict_object(json!({
                "action":{"type":"string","enum":[action]},
                "request_admission":{"type":"string","enum":[admission],"description":"The host's admitted original-request scope, not a disputed derived obligation or new permission."},
                "next_step":{"type":"string","minLength":1,"description":"Concrete work within the original request and current owner constraints, using the already offered executor grant."}
            })));
        }
    }
    super::strict_object(json!({"choice":{"anyOf":variants}}))
}

pub(crate) fn parse(
    message: &str,
    actions: &[Action],
    requirements: &[String],
    request_admission: Option<Digest>,
) -> Result<Choice, WorkerError> {
    if message.len() > 64 << 10 {
        return Err(WorkerError::InputBudget(
            "continuation response exceeds 64 KiB".into(),
        ));
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Response {
        choice: Choice,
    }
    let value: Value =
        serde_json::from_str(message).map_err(|e| WorkerError::Malformed(e.to_string()))?;
    if value["choice"].get("requirements").is_some()
        && value["choice"].get("request_admission").is_some()
    {
        return Err(WorkerError::Malformed(
            "select either obligation scopes or the original request admission".into(),
        ));
    }
    let Response { mut choice } =
        serde_json::from_value(value).map_err(|e| WorkerError::Malformed(e.to_string()))?;
    if !actions.contains(&choice.action()) {
        return Err(WorkerError::Malformed(
            "continuation action was not offered".into(),
        ));
    }
    match &mut choice {
        Choice::Rest { reason } | Choice::Check { reason } | Choice::Verify { reason }
            if !reason.trim().is_empty() => {}
        Choice::Diagnose { question } if !question.trim().is_empty() => {}
        Choice::Resume {
            requirements: ids,
            request_admission: requested,
            next_step,
        }
        | Choice::Successor {
            requirements: ids,
            request_admission: requested,
            next_step,
        } if !next_step.trim().is_empty()
            && match requested.as_ref() {
                Some(value) => ids.is_empty() && Some(*value) == request_admission,
                None => !ids.is_empty() && ids.iter().all(|id| requirements.contains(id)),
            } =>
        {
            // Repeating the same scope adds no authority and is not a failure.
            let mut seen = std::collections::BTreeSet::new();
            ids.retain(|id| seen.insert(id.clone()));
        }
        _ => {
            return Err(WorkerError::Malformed(
                "continuation requires nonempty reasoning and offered work scopes".into(),
            ));
        }
    }
    Ok(choice)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strategy_choice_cannot_add_scope_or_execution_authority() {
        let actions = [Action::Resume, Action::Rest];
        let allowed = ["R1".to_string()];
        let choice = |action, ids| {
            json!({"choice":{"action":action,"requirements":ids,"next_step":"Inspect the retained case."}}).to_string()
        };
        assert!(parse(&choice("resume", json!(["R1"])), &actions, &allowed, None).is_ok());
        assert!(
            parse(
                r#"{"choice":{"action":"rest","reason":"No useful work available."}}"#,
                &actions,
                &allowed,
                None
            )
            .is_ok()
        );
        for (action, ids) in [
            ("successor", json!(["R1"])),
            ("resume", json!(["R2"])),
            ("resume", json!([])),
            ("rest", json!(["R1"])),
        ] {
            assert!(parse(&choice(action, ids), &actions, &allowed, None).is_err());
        }
        let duplicate = parse(
            &choice("resume", json!(["R1", "R1"])),
            &actions,
            &allowed,
            None,
        )
        .unwrap();
        assert!(matches!(duplicate, Choice::Resume { requirements, .. } if requirements == ["R1"]));
    }

    #[test]
    fn original_request_scope_requires_the_exact_offered_admission_and_action() {
        let admission = Digest::of(b"original accepted request");
        let choice = |hash| {
            json!({"choice":{"action":"resume","request_admission":hash,"next_step":"Continue the original request."}}).to_string()
        };
        assert!(
            parse(
                &choice(admission),
                &[Action::Resume, Action::Rest],
                &[],
                Some(admission)
            )
            .is_ok()
        );
        assert!(parse(&choice(admission), &[Action::Rest], &[], Some(admission)).is_err());
        assert!(parse(&choice(admission), &[Action::Resume], &[], None).is_err());
        assert!(
            parse(
                &choice(Digest::of(b"another request")),
                &[Action::Resume],
                &[],
                Some(admission)
            )
            .is_err()
        );
        let mixed=json!({"choice":{"action":"resume","requirements":["R1"],"request_admission":admission,"next_step":"mixed"}}).to_string();
        assert!(parse(&mixed, &[Action::Resume], &["R1".into()], Some(admission)).is_err());
        let shape = schema(&[Action::Resume, Action::Rest], &[], Some(admission));
        assert!(shape.to_string().contains("request_admission"));
        assert!(!shape.to_string().contains("\"requirements\""));
    }
}
