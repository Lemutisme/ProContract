//! Preserve the executable's actual launch when a dynamic linker was invoked
//! explicitly. `/proc/self/exe` names that linker, not the running Rust program.
//!
//! Startup observations supply the program and command prefix. No repository,
//! model request, environment override or argv0 spelling chooses an executable.

use std::ffi::CStr;
use std::ffi::OsStr;
use std::ffi::OsString;
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::path::PathBuf;

pub(super) const DISPATCH_ENV: &str = "CODEX_INTERNAL_ARG0_DISPATCH";

pub(super) enum SelfExecutable {
    Direct(PathBuf),
    Interpreted {
        interpreter: PathBuf,
        arguments: Vec<OsString>,
        program: PathBuf,
    },
}

impl SelfExecutable {
    pub(super) fn detect() -> io::Result<Self> {
        let current_exe = std::env::current_exe()?;
        // The process's libc exposes the loader-adjusted auxiliary vector. A
        // directly executed dynamic program has AT_BASE != 0; explicit loader
        // startup has AT_BASE == 0 and AT_EXECFN names the loaded program.
        // SAFETY: getauxval returns a process-lifetime, NUL-terminated string
        // for AT_EXECFN, or zero when unavailable. We never dereference zero.
        let execfn = unsafe { libc::getauxval(libc::AT_EXECFN) };
        let base = unsafe { libc::getauxval(libc::AT_BASE) };
        if base != 0 || execfn == 0 {
            return Ok(Self::Direct(current_exe));
        }
        let program = unsafe { CStr::from_ptr(execfn as *const libc::c_char) };
        let program = PathBuf::from(OsStr::from_bytes(program.to_bytes()));
        let program = program.canonicalize()?;
        if program == current_exe {
            return Ok(Self::Direct(current_exe));
        }

        let cmdline = std::fs::read("/proc/self/cmdline")?;
        let original = cmdline
            .strip_suffix(&[0])
            .unwrap_or(&cmdline)
            .split(|byte| *byte == 0)
            .map(|bytes| OsString::from_vec(bytes.to_vec()))
            .collect::<Vec<_>>();
        Self::from_interpreted_startup(
            current_exe,
            program,
            &std::env::args_os().collect::<Vec<_>>(),
            &original,
            &std::env::current_dir()?,
        )
    }

    fn from_interpreted_startup(
        interpreter: PathBuf,
        program: PathBuf,
        visible: &[OsString],
        original: &[OsString],
        cwd: &Path,
    ) -> io::Result<Self> {
        let error = || {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "cannot bind executable to its interpreter launch",
            )
        };
        // libc removes interpreter options from argv. Match the *whole* remaining
        // argument suffix, not a filename occurrence in an arbitrary option value.
        let index = original
            .len()
            .checked_sub(visible.len())
            .ok_or_else(error)?;
        if visible.is_empty() || index == 0 || original[index + 1..] != visible[1..] {
            return Err(error());
        }
        let observed = Path::new(&original[index]);
        let observed = if observed.is_absolute() {
            observed.to_path_buf()
        } else {
            cwd.join(observed)
        };
        if observed.canonicalize()? != program {
            return Err(error());
        }
        let mut arguments = original[1..index].to_vec();
        normalize_loader_paths(&mut arguments, cwd);
        Ok(Self::Interpreted {
            interpreter,
            arguments,
            program,
        })
    }

    pub(super) fn direct_path(&self) -> Option<PathBuf> {
        match self {
            Self::Direct(path) => Some(path.clone()),
            Self::Interpreted { .. } => None,
        }
    }

    pub(super) fn write_alias(&self, path: &Path, dispatch: Option<&str>) -> io::Result<()> {
        match self {
            Self::Direct(exe) => std::os::unix::fs::symlink(exe, path),
            Self::Interpreted {
                interpreter,
                arguments,
                program,
            } => {
                let mut script = b"#!/bin/sh\n".to_vec();
                if let Some(dispatch) = dispatch {
                    script.extend_from_slice(DISPATCH_ENV.as_bytes());
                    script.push(b'=');
                    quote_shell(OsStr::new(dispatch), &mut script);
                    script.push(b' ');
                }
                script.extend_from_slice(b"exec ");
                quote_shell(interpreter.as_os_str(), &mut script);
                for arg in arguments {
                    script.push(b' ');
                    quote_shell(arg, &mut script);
                }
                script.push(b' ');
                quote_shell(program.as_os_str(), &mut script);
                script.extend_from_slice(b" \"$@\"\n");
                std::fs::write(path, script)?;
                std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
            }
        }
    }
}

fn quote_shell(value: &OsStr, out: &mut Vec<u8>) {
    out.push(b'\'');
    for byte in value.as_bytes() {
        if *byte == b'\'' {
            out.extend_from_slice(b"'\\''");
        } else {
            out.push(*byte);
        }
    }
    out.push(b'\'');
}

fn normalize_loader_paths(arguments: &mut [OsString], cwd: &Path) {
    // Relative loader paths refer to the startup cwd, not to a future tool's
    // workspace. Dynamic tokens remain interpreted by the loader itself.
    let mut index = 0;
    while index + 1 < arguments.len() {
        let library_path = arguments[index] == "--library-path";
        if library_path || arguments[index] == "--preload" || arguments[index] == "--audit" {
            let mut result = Vec::new();
            for part in arguments[index + 1].as_bytes().split_inclusive(|c| {
                *c == b':' || (library_path && *c == b';') || (!library_path && *c == b' ')
            }) {
                let (part, separator) = match part.last() {
                    Some(c)
                        if *c == b':'
                            || (library_path && *c == b';')
                            || (!library_path && *c == b' ') =>
                    {
                        (&part[..part.len() - 1], Some(*c))
                    }
                    _ => (part, None),
                };
                let path = Path::new(OsStr::from_bytes(part));
                let origin_rooted = part == b"$ORIGIN"
                    || part.starts_with(b"$ORIGIN/")
                    || part == b"${ORIGIN}"
                    || part.starts_with(b"${ORIGIN}/");
                if !path.is_absolute() && !origin_rooted && (library_path || part.contains(&b'/')) {
                    let absolute = if part.is_empty() {
                        cwd.to_path_buf()
                    } else {
                        cwd.join(path)
                    };
                    result.extend_from_slice(absolute.as_os_str().as_bytes());
                } else {
                    result.extend_from_slice(part);
                }
                if let Some(separator) = separator {
                    result.push(separator);
                }
            }
            // A trailing empty search directory, like any empty component,
            // means the startup working directory.
            if library_path
                && (arguments[index + 1].is_empty()
                    || matches!(arguments[index + 1].as_bytes().last(), Some(b':' | b';')))
            {
                result.extend_from_slice(cwd.as_os_str().as_bytes());
            }
            arguments[index + 1] = OsString::from_vec(result);
            index += 2;
        } else if matches!(
            arguments[index].to_str(),
            Some("--argv0" | "--inhibit-rpath" | "--glibc-hwcaps-mask" | "--glibc-hwcaps-prepend")
        ) {
            index += 2;
        } else {
            index += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;
    use std::process::Command;

    fn strings(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn startup_binding_uses_auxiliary_program_and_full_argument_suffix() -> io::Result<()> {
        let root = tempfile::tempdir()?;
        let program = root.path().join("real program");
        std::fs::write(&program, b"fixture")?;
        let program = program.canonicalize()?;
        let visible = strings(&["forged-argv0", "exec-server", "--listen", "stdio"]);
        let mut original = strings(&[
            "/lib/loader",
            "--library-path",
            "relative libs",
            "--argv0",
            "forged-argv0",
        ]);
        original.push(program.as_os_str().to_owned());
        original.extend_from_slice(&visible[1..]);
        let launch = SelfExecutable::from_interpreted_startup(
            PathBuf::from("/lib/loader"),
            program.clone(),
            &visible,
            &original,
            root.path(),
        )?;
        let SelfExecutable::Interpreted {
            program: resolved,
            arguments,
            ..
        } = launch
        else {
            panic!("expected loader launch")
        };
        assert_eq!(resolved, program);
        assert_eq!(arguments[1], root.path().join("relative libs").as_os_str());
        assert_eq!(arguments[3], "forged-argv0");

        let last = original.len() - 1;
        original[last] = OsString::from("different");
        assert!(
            SelfExecutable::from_interpreted_startup(
                PathBuf::from("/lib/loader"),
                program,
                &visible,
                &original,
                root.path()
            )
            .is_err()
        );
        Ok(())
    }

    #[test]
    fn program_name_inside_an_option_is_not_the_program_boundary() -> io::Result<()> {
        let root = tempfile::tempdir()?;
        let program = root.path().join("codex");
        std::fs::write(&program, b"fixture")?;
        let program = program.canonicalize()?;
        std::fs::write(root.path().join("other-program"), b"another fixture")?;
        let visible = strings(&["codex", "exec-server"]);
        let original = vec![
            OsString::from("loader"),
            OsString::from("--argv0"),
            program.clone().into_os_string(),
            OsString::from("other-program"),
            OsString::from("exec-server"),
        ];
        assert!(
            SelfExecutable::from_interpreted_startup(
                PathBuf::from("/lib/loader"),
                program,
                &visible,
                &original,
                root.path()
            )
            .is_err()
        );
        Ok(())
    }

    #[test]
    fn relative_loader_paths_keep_startup_meaning_and_dynamic_tokens() {
        let mut args = strings(&[
            "--argv0",
            "--library-path",
            "--library-path",
            ":lib;$ORIGIN/../lib:/absolute:$LIB:",
            "--preload",
            "./one.so ./two.so:libc.so",
            "--audit",
            "./audit.so",
        ]);
        normalize_loader_paths(&mut args, Path::new("/startup"));
        assert_eq!(
            args,
            strings(&[
                "--argv0",
                "--library-path",
                "--library-path",
                "/startup:/startup/lib;$ORIGIN/../lib:/absolute:/startup/$LIB:/startup",
                "--preload",
                "/startup/./one.so /startup/./two.so:libc.so",
                "--audit",
                "/startup/./audit.so"
            ])
        );
    }

    #[test]
    fn direct_launch_keeps_native_alias_and_real_executable() -> io::Result<()> {
        let root = tempfile::tempdir()?;
        let binary = root.path().join("real");
        std::fs::write(&binary, b"fixture")?;
        let launch = SelfExecutable::Direct(binary.clone());
        let alias = root.path().join("apply_patch");
        launch.write_alias(&alias, Some("apply_patch"))?;
        assert_eq!(std::fs::read_link(alias)?, binary);
        assert_eq!(launch.direct_path(), Some(binary));
        Ok(())
    }

    #[test]
    fn launcher_preserves_argument_bytes_stdin_and_working_directory() -> io::Result<()> {
        let root = tempfile::tempdir()?;
        let program = root.path().join("program ' with spaces");
        std::fs::write(
            &program,
            b"printf '%s\\n' \"$CODEX_INTERNAL_ARG0_DISPATCH\" \"$PWD\" \"$1\"; cat\n",
        )?;
        // /bin/sh stands in for an interpreter here: the launcher must preserve
        // its prefix, program path, child argument and stdin without shell eval.
        let launch = SelfExecutable::Interpreted {
            interpreter: PathBuf::from("/bin/sh"),
            arguments: vec![],
            program,
        };
        let alias = root.path().join("alias");
        launch.write_alias(&alias, Some("apply_patch"))?;
        let cwd = root.path().join("workspace");
        std::fs::create_dir(&cwd)?;
        let mut child = Command::new(&alias)
            .arg("literal ' $(exit 23) $value")
            .current_dir(&cwd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()?;
        use std::io::Write;
        child.stdin.take().unwrap().write_all(b"patch body\n")?;
        let output = child.wait_with_output()?;
        assert!(output.status.success());
        assert_eq!(
            output.stdout,
            format!(
                "apply_patch\n{}\nliteral ' $(exit 23) $value\npatch body\n",
                cwd.display()
            )
            .as_bytes()
        );
        assert_eq!(
            std::fs::metadata(alias)?.permissions().mode() & 0o777,
            0o700
        );
        Ok(())
    }

    #[test]
    fn internal_dispatch_hint_selects_only_existing_modes() {
        for name in ["apply_patch", "applypatch", "codex-linux-sandbox"] {
            assert_eq!(
                crate::internal_dispatch_name(OsStr::new(name)),
                Some(name.to_string())
            );
        }
        for name in [
            "",
            "/tmp/apply_patch",
            "sh",
            "apply_patch; echo injected",
            "--codex-fs-helper",
            "codex-execve-wrapper",
        ] {
            assert_eq!(crate::internal_dispatch_name(OsStr::new(name)), None);
        }
    }

    #[test]
    fn dispatch_hint_is_consumed_before_descendants() -> io::Result<()> {
        const CHILD: &str = "CODEX_ARG0_HINT_TEST_CHILD";
        if std::env::var_os(CHILD).is_some() {
            assert_eq!(
                crate::take_internal_dispatch_name(),
                Some("apply_patch".to_string())
            );
            assert!(std::env::var_os(DISPATCH_ENV).is_none());
            assert!(
                Command::new("/bin/sh")
                    .args(["-c", "test -z \"${CODEX_INTERNAL_ARG0_DISPATCH+x}\""])
                    .status()?
                    .success()
            );
            return Ok(());
        }
        let status = Command::new(std::env::current_exe()?)
            .args([
                "--exact",
                "self_executable::tests::dispatch_hint_is_consumed_before_descendants",
            ])
            .env(CHILD, "1")
            .env(DISPATCH_ENV, "apply_patch")
            .status()?;
        assert!(status.success());
        Ok(())
    }
}
