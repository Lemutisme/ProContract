use super::packet;
use codex_thread_context_ids_test_support::thread_context_ids;
use pretty_assertions::assert_eq;

#[test]
fn fresh_context_is_bounded_and_preserves_the_complete_task_and_lessons() {
    let text = "原始任务与失败证据\n".repeat(/*n*/ 500);
    let request = packet(&text).unwrap();
    let parts = request
        .additional_context
        .as_ref()
        .expect("successor packet carries its additional context");
    assert!(parts.values().all(|part| part.value.len() <= 768));
    assert_eq!(
        parts.values().map(|p| p.value.as_str()).collect::<String>(),
        text
    );
    assert!(packet(&"x".repeat(/*n*/ 64001)).is_err());
}

#[test]
fn admission_uses_complete_originals_and_keeps_later_restrictions_in_order() {
    let item_id_generator = thread_context_ids();
    use codex_history::{
        RetainedContext, RetainedInputSource, RetainedUserMessage, UserInputOrigin,
    };
    let mut retained = RetainedContext::default();
    assert!(super::owner_context(&retained).is_err());
    let message = RetainedUserMessage {
        turn_id: "task".into(),
        message_id: Some("original".into()),
        text: "excerpt".into(),
        complete: false,
        origin: UserInputOrigin::User,
        phase: None,
    };
    retained.record_user_message(
        message.clone(),
        RetainedInputSource::Local(Some(0)),
        &item_id_generator,
    );
    assert!(super::owner_context(&retained).is_err());
    let original = format!("{}Preserve unrelated work.", "task detail ".repeat(400));
    retained.record_user_message_with_original(
        message.clone(),
        RetainedInputSource::Local(Some(0)),
        Some(original.clone()),
        &item_id_generator,
    );
    retained.record_user_message(
        RetainedUserMessage {
            message_id: Some("restriction".into()),
            text: "Do not deploy.".into(),
            complete: true,
            ..message
        },
        RetainedInputSource::Local(Some(1)),
        &item_id_generator,
    );
    let context = super::owner_context(&retained).unwrap();
    assert_eq!(context[0]["text"], original);
    assert_eq!(context[1]["text"], "Do not deploy.");
    retained.mark_user_messages_incomplete();
    assert!(super::owner_context(&retained).is_err());
}
