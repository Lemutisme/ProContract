#!/usr/bin/env python3
"""Install the selected native package under a new name; never import live state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pin(path):
    path = Path(path)
    resolved = path.resolve(strict=True)
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    return {"path": str(path), "resolved": str(resolved), "sha256": sha(resolved),
            "link_stat": {k: getattr(path.lstat(), k) for k in fields},
            "target_stat": {k: getattr(resolved.stat(), k) for k in fields}}


def install(package, prefix, entry, admission, protected_home, live_codex):
    package, prefix, entry = Path(package).resolve(strict=True), Path(prefix).absolute(), Path(entry).absolute()
    if entry.name != "procodex":
        raise ValueError("the only PATH entry created by this installer is named procodex")
    if os.path.lexists(prefix) or os.path.lexists(entry):
        raise FileExistsError("installation prefix and procodex entry must both be new")
    if any(p.is_symlink() for p in (*prefix.parents, *entry.parents)):
        raise ValueError("installation paths must not be redirected")
    protected = Path(protected_home).resolve()
    if prefix.is_relative_to(protected) or protected.is_relative_to(prefix):
        raise ValueError("installation must be separate from live Codex state")
    gate = json.loads(Path(admission).read_text())
    if gate.get("safe_to_install") is not True:
        raise ValueError("installation requires the retained completed admission")
    for evidence in gate["evidence"]:
        if sha(evidence["path"]) != evidence["sha256"]:
            raise ValueError("installation admission evidence changed")
    source = json.loads((package / "build-source.json").read_text())
    if source["native_source_commit"] != gate["native_source_commit"]:
        raise ValueError("admission names another native build")
    for name, expected in source["files"].items():
        target = package / name
        if target.is_symlink() or not target.resolve().is_relative_to(package) or sha(target) != expected:
            raise ValueError(f"package file changed: {name}")
    if sha(package / "build-source.json") != gate["package_manifest_sha256"]:
        raise ValueError("admission names another package manifest")
    before = pin(live_codex)
    prefix.mkdir(mode=0o700, parents=True)
    shutil.copytree(package, prefix / "package")
    shutil.copy2(Path(__file__).with_name("procodex_standalone.py"), prefix / "procodex_standalone.py")
    home = prefix / "home"
    home.mkdir(mode=0o700)
    (home / "config.toml").write_text("# Independent level-1 state. No credentials were copied.\n[features]\npro_contract = false\n")
    (home / "config.toml").chmod(0o600)
    identity = {"schema": "procodex-level1/1", "native_source_commit": source["native_source_commit"],
                "package_source_commit": source["source_commit"], "package_version": source["version"],
                "binary_sha256": sha(prefix / "package/bin/codex"),
                "wrapper_sha256": sha(prefix / "procodex_standalone.py"),
                "installer_source_commit": subprocess.check_output(["git", "-C", str(Path(__file__).resolve().parent.parent), "rev-parse", "HEAD"], text=True).strip()}
    installation = {"identity": identity, "prefix": str(prefix), "entry": str(entry), "default_home": str(home),
                    "protected_home": str(protected), "admission": str(Path(admission).resolve()),
                    "admission_sha256": sha(admission), "live_codex_before": before,
                    "credentials_copied": False, "default_procontract_enabled": False}
    (prefix / "installation.json").write_text(json.dumps(installation, indent=2) + "\n")
    launcher = "#!/bin/sh\n# PROCODEX_LEVEL1_ENTRY\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(prefix / "procodex_standalone.py")) + ' "$@"\n'
    entry.parent.mkdir(parents=True, exist_ok=True)
    with entry.open("x") as stream:
        stream.write(launcher)
    entry.chmod(0o755)
    after = pin(live_codex)
    installation.update(live_codex_after=after, live_codex_unchanged=before == after,
                        entry_sha256=sha(entry), installed=True)
    (prefix / "installation.json").write_text(json.dumps(installation, indent=2) + "\n")
    if before != after:
        raise RuntimeError("live Codex identity changed during installation; inspect retained receipt")
    return installation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "prefix", "entry", "admission", "protected-home", "live-codex"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.package, args.prefix, args.entry, args.admission,
                             args.protected_home, args.live_codex), indent=2))


if __name__ == "__main__":
    main()
