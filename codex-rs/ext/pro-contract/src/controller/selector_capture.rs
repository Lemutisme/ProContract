//! Opt-in native selector capture. The effective wire is retained by the paired gateway hook.
//! This hook binds the actual isolated worker, native archive and input configuration; it
//! does not create an Operations execution, a fork receipt, or a restoration claim.

use std::fs::OpenOptions;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use codex_extension_api::{
    ExtensionFuture, ModelRequestContributor, ModelRequestInput, ModelRequestKind,
    ModelResponseInterceptor,
};
use codex_pro_contract::Digest;
use serde_json::{Value, json};

use super::archive::EvidenceAccess;
use super::archive_export::{ArchiveCaptureLimits, SelectorArchiveReader};
use crate::SelectorCaptureSettings;

const SCHEMA: &str = "native-selector-capture/v1";

fn retain(directory: &Path, body: &Value) -> Result<String, String> {
    let bytes = serde_json::to_vec(body).map_err(|e| e.to_string())?;
    let hash = Digest::of(&bytes).to_string();
    let path = directory.join(format!("{hash}.json"));
    match OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&path)
    {
        Ok(mut file) => {
            file.write_all(&bytes)
                .and_then(|_| file.sync_all())
                .map_err(|e| e.to_string())?;
            std::fs::File::open(directory)
                .and_then(|f| f.sync_all())
                .map_err(|e| e.to_string())?;
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            if std::fs::symlink_metadata(&path)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_symlink()
                || std::fs::read(&path).map_err(|e| e.to_string())? != bytes
            {
                return Err("native capture object differs".into());
            }
        }
        Err(e) => return Err(e.to_string()),
    }
    Ok(hash)
}

#[derive(Default)]
struct State {
    manifest: Option<String>,
    thread: Option<String>,
    next_request: u64,
    failed: bool,
}

pub(crate) struct SelectorCapture {
    directory: PathBuf,
    reader: SelectorArchiveReader,
    template: Value,
    state: Mutex<State>,
}

impl std::fmt::Debug for SelectorCapture {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SelectorCapture")
            .field("directory", &self.directory)
            .finish_non_exhaustive()
    }
}

impl SelectorCapture {
    pub(crate) async fn prepare(
        settings: &SelectorCaptureSettings,
        access: EvidenceAccess,
        prompt: &str,
        response_schema: &Value,
        instructions: &str,
        model: Option<&str>,
        effort: Value,
    ) -> Result<Self, String> {
        if !settings.directory.is_absolute() || settings.max_bytes == 0 || settings.max_entries == 0
        {
            return Err(
                "selector capture requires an absolute host directory and positive export limits"
                    .into(),
            );
        }
        let directory = std::fs::canonicalize(&settings.directory).map_err(|e| e.to_string())?;
        if !directory.is_dir() || model.is_none() || effort.is_null() {
            return Err(
                "selector capture requires an existing host directory and pinned model/effort"
                    .into(),
            );
        }
        let reader = SelectorArchiveReader::from_access(access).await?;
        let snapshot = reader
            .export(ArchiveCaptureLimits {
                max_entries: settings.max_entries,
                max_bytes: settings.max_bytes,
            })
            .await?;
        let archive = retain(&directory, &snapshot)?;
        let template = json!({
            "schema": SCHEMA, "archive_sha256": archive, "boundary": snapshot["boundary"],
            "prompt": prompt, "prompt_sha256": Digest::of(prompt.as_bytes()),
            "response_schema": response_schema, "instructions": instructions,
            "model": model, "reasoning_effort": effort,
            "entry": "initial_native_selector_exchange",
            "source": "actual isolated worker before first generation; no historical suffix restored",
        });
        Ok(Self {
            directory,
            reader,
            template,
            state: Mutex::new(State::default()),
        })
    }

    async fn admit_request(&self, thread: &str) -> Result<(), String> {
        {
            let state = self.state.lock().map_err(|_| "capture lock poisoned")?;
            if state.failed {
                return Err("selector capture previously failed".into());
            }
            if let Some(known) = &state.thread {
                if known != thread {
                    return Err("capture is attached to another native thread".into());
                }
            }
            // Only the initial request claims the initial boundary. Retried requests and
            // later tool-loop requests keep distinct ordinals, never new initial samples.
            if state.next_request > 0 {
                return Ok(());
            }
        }
        self.reader.check_boundary().await?;
        let mut manifest = self.template.clone();
        manifest["native_thread_id"] = json!(thread);
        let identity = retain(&self.directory, &manifest)?;
        let mut state = self.state.lock().map_err(|_| "capture lock poisoned")?;
        state.manifest = Some(identity);
        state.thread = Some(thread.to_string());
        Ok(())
    }

    pub(crate) fn closure(
        &self,
        thread: &str,
        shutdown_confirmed: bool,
        outcome: &str,
    ) -> Result<(), String> {
        let state = self.state.lock().map_err(|_| "capture lock poisoned")?;
        let body = json!({"schema":"native-selector-closure/v1", "native_thread_id":thread,
            "capture":state.manifest, "request_metadata_issued":state.next_request,
            "shutdown_confirmed":shutdown_confirmed, "outcome":outcome,
            "meaning":"Native worker shutdown observation; not an Operations closure or allocation"});
        retain(&self.directory, &body).map(|_| ())
    }
}

impl ModelRequestContributor for SelectorCapture {
    fn budget_exhausted<'a>(&'a self, thread: &'a str) -> ExtensionFuture<'a, bool> {
        Box::pin(async move {
            match self.admit_request(thread).await {
                Ok(()) => false,
                Err(reason) => {
                    if let Ok(mut state) = self.state.lock() {
                        state.failed = true;
                    }
                    let _ = retain(
                        &self.directory,
                        &json!({"schema":"native-selector-capture-error/v1",
                        "native_thread_id":thread,"reason":reason}),
                    );
                    tracing::warn!("selector capture refused generation: {reason}");
                    true
                }
            }
        })
    }

    fn request(&self, input: ModelRequestInput<'_>) -> Option<Box<dyn ModelResponseInterceptor>> {
        if input.kind != ModelRequestKind::Generation {
            return None;
        }
        let mut state = self.state.lock().expect("capture lock poisoned");
        // Core calls the awaited admission check before preparing each generation.
        if state.failed || state.thread.as_deref() != Some(input.thread_id) {
            return None;
        }
        let Some(manifest) = state.manifest.clone() else {
            return None;
        };
        let metadata = input.client_metadata.get_or_insert_default();
        metadata.insert("procontract_capture".into(), manifest);
        metadata.insert("procontract_capture_thread".into(), input.thread_id.into());
        metadata.insert(
            "procontract_capture_request".into(),
            state.next_request.to_string(),
        );
        state.next_request += 1;
        None
    }
}
