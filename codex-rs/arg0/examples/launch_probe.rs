//! Small native qualification entrypoint. It uses the real dispatch/alias
//! implementation and runs one caller-selected local command while its guard is
//! alive; it is not included in the Codex distribution.

fn main() -> anyhow::Result<()> {
    codex_arg0::arg0_dispatch_or_else(|paths| async move {
        let mut args = std::env::args_os().skip(1);
        let command = args
            .next()
            .ok_or_else(|| anyhow::anyhow!("a local qualification command is required"))?;
        let status = std::process::Command::new(command)
            .args(args)
            .env(
                "CODEX_QUALIFIED_SELF_EXE",
                paths
                    .codex_self_exe
                    .ok_or_else(|| anyhow::anyhow!("no re-exec path"))?,
            )
            .status()?;
        anyhow::ensure!(status.success(), "qualification command failed: {status}");
        Ok(())
    })
}
