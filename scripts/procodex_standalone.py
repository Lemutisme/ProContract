#!/usr/bin/env python3
"""Level-1 native CLI entry with separate state and explicit build identity."""
import json
import os
from pathlib import Path
import subprocess
import sys


def selected_home(environment, default, protected):
    supplied = environment.get("CODEX_HOME")
    if supplied is not None and not supplied.strip():
        raise ValueError("CODEX_HOME must be absent or name a separate directory")
    selected = Path(supplied).expanduser().resolve() if supplied is not None else Path(default).resolve()
    protected = Path(protected).resolve()
    if selected == protected or selected.is_relative_to(protected) or protected.is_relative_to(selected):
        raise ValueError("procodex refuses the live Codex home; unset CODEX_HOME or select a private directory")
    return selected


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    prefix = Path(__file__).resolve().parent
    installation = json.loads((prefix / "installation.json").read_text())
    try:
        home = selected_home(os.environ, installation["default_home"], installation["protected_home"])
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    binary = prefix / "package/bin/codex"
    if args == ["--build-info"]:
        print(json.dumps({**installation["identity"], "entry": installation["entry"],
                          "default_home": installation["default_home"], "effective_home": str(home)}, indent=2))
        return 0
    if args in (["--version"], ["-V"]):
        identity = installation["identity"]
        print(f"procodex level-1 native {identity['native_source_commit'][:12]} "
              f"package {identity['package_source_commit'][:12]} "
              f"sha256 {identity['binary_sha256'][:16]}", flush=True)
        return subprocess.call([str(binary), "--version"], env={**os.environ, "CODEX_HOME": str(home)})
    os.execve(binary, [str(binary), *args], {**os.environ, "CODEX_HOME": str(home)})


if __name__ == "__main__":
    raise SystemExit(main())
